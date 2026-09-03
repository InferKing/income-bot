from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta

from income_tg.config import get_settings
from income_tg.jobs.database_training import (
    TrainingTarget,
    _model_training_partitions,
    _select_confidence_threshold,
    _strategy_metrics,
)
from income_tg.models.dataset import (
    chronological_train_test,
    chronological_windows,
    load_labeled_dataset,
)
from income_tg.models.diagnostics import audit_features
from income_tg.models.evaluation import AdmissionCriteria
from income_tg.models.training import train_ensemble
from income_tg.storage.database import Database
from income_tg.storage.instruments import find_instrument

MODEL_WEIGHTS = {"forest": 0.0, "ensemble": 0.5, "logistic": 1.0}


async def run(args: argparse.Namespace) -> None:
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            instrument = await find_instrument(
                session, args.instrument, market_type="linear_perpetual"
            )
        if instrument is None:
            raise RuntimeError(f"Instrument not found: {args.instrument}")
        criteria = AdmissionCriteria()
        for horizon, minutes in _parse_horizons(args.horizons):
            duration = timedelta(minutes=minutes)
            target = TrainingTarget(instrument.id, horizon, duration)
            async with database.session_factory() as session:
                labeled = await load_labeled_dataset(
                    session,
                    instrument_id=instrument.id,
                    horizon=horizon,
                    horizon_duration=duration,
                    minimum_actionable_return=target.minimum_actionable_return,
                    candle_provider=target.candle_provider,
                )
            model_training, threshold_validation = _model_training_partitions(
                labeled, embargo=duration
            )
            _, test = chronological_train_test(labeled, embargo=duration)
            audits = audit_features(labeled, embargo=duration)
            print(
                json.dumps(
                    {
                        "type": "feature_audit",
                        "horizon": horizon,
                        "samples": len(labeled.dataset.timestamps),
                        "features": [item.as_dict() for item in audits],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            for model_name, weight in MODEL_WEIGHTS.items():
                model = train_ensemble(
                    model_training.dataset,
                    calibration_embargo=duration,
                    target_action_fraction=target.target_action_fraction,
                    logistic_weight=weight,
                )
                model.metadata["confidence_threshold"] = _select_confidence_threshold(
                    model, threshold_validation, target, criteria
                )
                metrics = _strategy_metrics(model, test, target)
                windows = tuple(
                    _strategy_metrics(model, window, target).net_return
                    for window in chronological_windows(test, criteria.walk_forward_windows)
                )
                actionable = int((test.dataset.targets != 0).sum())
                print(
                    json.dumps(
                        {
                            "type": "model_benchmark",
                            "horizon": horizon,
                            "model": model_name,
                            "threshold": model.confidence_threshold,
                            "samples": len(test.dataset.timestamps),
                            "actionable_labels": actionable,
                            "required_trades": criteria.required_closed_trades(actionable),
                            "trades": metrics.trades,
                            "net_return": metrics.net_return,
                            "profit_factor": metrics.profit_factor,
                            "max_drawdown": metrics.max_drawdown,
                            "win_rate": metrics.win_rate,
                            "long_trades": metrics.long_trades,
                            "short_trades": metrics.short_trades,
                            "profitable_windows": sum(value > 0 for value in windows),
                            "window_returns": windows,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    finally:
        await database.dispose()


def _parse_horizons(values: list[str]) -> tuple[tuple[str, int], ...]:
    result = []
    for value in values:
        if value.endswith("m"):
            minutes = int(value[:-1])
        elif value.endswith("h"):
            minutes = int(value[:-1]) * 60
        else:
            raise ValueError(f"Unsupported horizon: {value}")
        result.append((value, minutes))
    return tuple(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", default="BTC/USDT:PERP")
    parser.add_argument("--horizons", nargs="+", default=["15m", "1h", "4h"])
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
