from __future__ import annotations

import argparse
import asyncio
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from income_tg.config import get_settings
from income_tg.jobs.activation import FileModelActivator
from income_tg.jobs.database_training import (
    DatabaseCandidateEvaluator,
    DatabaseCandidateTrainer,
    PersistedRetrainingWorkflow,
    TrainingTarget,
)
from income_tg.jobs.retention import OrderbookRetentionJob, orderbook_retention_definition
from income_tg.jobs.retraining import (
    RetrainingRunner,
    RetrainingSkipped,
    RetrainingWorkflow,
    weekly_retraining_definition,
)
from income_tg.jobs.scheduler import AsyncScheduler
from income_tg.jobs.store import JsonJobStore
from income_tg.logging import configure_logging
from income_tg.models.evaluation import AdmissionCriteria
from income_tg.models.inference import EnsembleModel
from income_tg.models.registry import FileModelRegistry
from income_tg.storage.database import Database
from income_tg.storage.instruments import find_instrument
from income_tg.storage.trading_models import InstrumentRecord


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings.database_url)
    try:
        instrument = await _wait_for_instrument(database, args.instrument, run_once=args.run_once)
        target = TrainingTarget(
            instrument_id=instrument.id,
            horizon=args.horizon,
            horizon_duration=timedelta(minutes=args.horizon_minutes),
        )
        registry = FileModelRegistry(args.model_dir)
        criteria = AdmissionCriteria()
        base_workflow = RetrainingWorkflow(
            trainer=DatabaseCandidateTrainer(database.session_factory, target, criteria),
            evaluator=DatabaseCandidateEvaluator(database.session_factory, target, criteria),
            registry=registry,
            activator=FileModelActivator(registry),
            activation_check=_activation_check,
            criteria=criteria,
        )
        workflow = PersistedRetrainingWorkflow(
            base_workflow,
            database.session_factory,
            registry,
            target,
            criteria,
            minimum_new_labeled_points=(0 if args.run_once else 12),
        )
        if args.run_once:
            await workflow.run()
            return
        retraining_definition = weekly_retraining_definition(workflow, datetime.now(UTC))
        retention_job = OrderbookRetentionJob(
            database.session_factory,
            retention=timedelta(days=args.orderbook_retention_days),
            batch_size=args.orderbook_retention_batch_size,
            max_batches=args.orderbook_retention_max_batches,
        )
        retention_definition = orderbook_retention_definition(retention_job)
        scheduler = AsyncScheduler(
            JsonJobStore(args.state_file),
            (retention_definition, retraining_definition),
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, stop.set)
        bootstrap_task = asyncio.create_task(
            _bootstrap_until_champion(workflow, registry, stop),
            name="model-bootstrap",
        )
        try:
            await scheduler.serve(stop)
        finally:
            bootstrap_task.cancel()
            await asyncio.gather(bootstrap_task, return_exceptions=True)
    finally:
        await database.dispose()


async def _activation_check(model: EnsembleModel) -> bool:
    return bool(model.feature_names) and model.metadata.get("samples", 0) >= 40


async def _wait_for_instrument(
    database: Database,
    symbol: str,
    *,
    run_once: bool,
) -> InstrumentRecord:
    logger = structlog.get_logger()
    while True:
        async with database.session_factory() as session:
            instrument = await find_instrument(
                session,
                symbol,
                market_type="linear_perpetual",
            )
        if instrument is not None:
            return instrument
        if run_once:
            raise RuntimeError(f"Инструмент еще не собран: {symbol}")
        logger.warning("scheduler_waiting_for_instrument", instrument=symbol)
        await asyncio.sleep(5)


async def _bootstrap_until_champion(
    workflow: RetrainingRunner,
    registry: FileModelRegistry,
    stop: asyncio.Event,
) -> None:
    logger = structlog.get_logger()
    while not stop.is_set():
        try:
            registry.load_champion()
            return
        except FileNotFoundError:
            pass
        try:
            outcome = await workflow.run()
            logger.info("bootstrap_retraining_completed", outcome=outcome.summary())
            if outcome.decision.accepted:
                return
        except RetrainingSkipped as error:
            logger.info("bootstrap_retraining_skipped", reason=str(error))
        except Exception:
            logger.exception("bootstrap_retraining_waiting_for_data")
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600)
        except TimeoutError:
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly model retraining scheduler")
    parser.add_argument("--instrument", default="BTC/USDT:PERP")
    parser.add_argument("--horizon", default="15m")
    parser.add_argument("--horizon-minutes", type=int, default=15)
    parser.add_argument("--model-dir", type=Path, default=Path("models").resolve())
    parser.add_argument(
        "--state-file", type=Path, default=Path("models/scheduler-state.json").resolve()
    )
    parser.add_argument("--orderbook-retention-days", type=int, default=7)
    parser.add_argument("--orderbook-retention-batch-size", type=int, default=10_000)
    parser.add_argument("--orderbook-retention-max-batches", type=int, default=100)
    parser.add_argument("--run-once", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
