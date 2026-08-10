from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import PortfolioKind, TradeSide
from income_tg.config import get_settings
from income_tg.features.pipeline import CandleInput, FeatureVector, MarketObservation
from income_tg.models.inference import ModelPrediction
from income_tg.models.registry import FileModelRegistry
from income_tg.paper_trading.engine import PaperExecutionEngine
from income_tg.paper_trading.models import (
    ExecutionSettings,
    InstrumentKind,
    MarketSnapshot,
    Order,
    OrderSide,
    OrderType,
    PaperPosition,
    PositionSide,
)
from income_tg.paper_trading.repository import PaperTradingRepository
from income_tg.portfolio.service import PortfolioService
from income_tg.risk.engine import RiskEngine
from income_tg.risk.models import ExecutionCosts, PortfolioRiskState, RiskLimits
from income_tg.signals.domain import MarketType, SignalAction, SignalCandidate
from income_tg.signals.policy import SignalPolicy
from income_tg.signals.service import SignalService
from income_tg.storage.database import Database
from income_tg.storage.models import Portfolio, User
from income_tg.storage.trading_models import (
    DerivativeMetricRecord,
    EquityPointRecord,
    FeatureVectorRecord,
    FxRateRecord,
    InstrumentRecord,
    MarketCandleRecord,
    ModelVersionRecord,
    OrderbookSnapshotRecord,
    PaperPositionRecord,
    PredictionRecord,
    RiskProfileRecord,
)
from income_tg.worker.engine import TradingWorker
from income_tg.worker.models import TradingWorkItem


@dataclass(frozen=True, slots=True)
class StoredFeatureBuilder:
    vector: FeatureVector

    def build(self, observation: MarketObservation) -> FeatureVector:
        if (
            observation.instrument != self.vector.instrument
            or observation.as_of != self.vector.as_of
        ):
            raise ValueError("stored feature vector does not belong to the observation")
        return self.vector


class DatabasePredictionRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        item: TradingWorkItem,
        prediction: ModelPrediction,
        vector: FeatureVector,
    ) -> UUID | None:
        model_version = await self._session.scalar(
            select(ModelVersionRecord).where(ModelVersionRecord.version == prediction.model_version)
        )
        if model_version is None:
            return None
        record = PredictionRecord(
            model_version_id=model_version.id,
            instrument_id=item.instrument_id,
            horizon=item.horizon,
            as_of=prediction.as_of,
            data_cutoff=vector.data_cutoff,
            probability_up=prediction.probability_up,
            probability_down=prediction.probability_down,
            confidence=prediction.confidence,
            contributions=[list(value) for value in prediction.contributions],
        )
        self._session.add(record)
        await self._session.flush()
        return record.id


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _paper_position(record: PaperPositionRecord, symbol: str) -> PaperPosition:
    return PaperPosition(
        position_id=record.position_key,
        symbol=symbol,
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide(record.side),
        quantity=record.quantity,
        entry_price=record.entry_price,
        leverage=record.leverage,
        margin=record.margin,
        stop_loss=record.stop_loss,
        take_profit=record.take_profit,
        opening_commission=record.opening_commission,
        funding_pnl=record.funding_pnl,
        opened_at=_aware(record.opened_at),
        liquidation_price=record.liquidation_price,
    )


def _equity(cash: Decimal, positions: list[PaperPosition], midpoint: Decimal) -> Decimal:
    total = cash
    for position in positions:
        direction = Decimal("-1") if position.side is PositionSide.SHORT else Decimal("1")
        unrealized = (midpoint - position.entry_price) * position.quantity * direction
        total += position.margin + unrealized + position.funding_pnl
    return total


async def _ensure_usdt_cash(
    service: PortfolioService,
    portfolio_id: object,
    *,
    rate: Decimal,
) -> dict[str, Decimal]:
    from uuid import UUID

    if not isinstance(portfolio_id, UUID):
        raise TypeError("portfolio_id must be UUID")
    balances = await service.get_balances(portfolio_id)
    rub = balances.get("RUB", Decimal("0"))
    if balances.get("USDT", Decimal("0")) <= 0 and rub > 0:
        await service.record_trade(
            portfolio_id,
            TradeSide.BUY,
            "USDT",
            "RUB",
            rub / rate,
            rate,
            "paper-initial-rub-to-usdt:v1",
        )
        balances = await service.get_balances(portfolio_id)
    return balances


async def _run_cycle(
    database: Database,
    *,
    instrument_symbol: str,
    horizon: str,
    model_dir: Path,
) -> str:
    settings = get_settings()
    model = FileModelRegistry(model_dir).load_champion()
    now = datetime.now(UTC)
    async with database.session() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == settings.require_owner_id())
        )
        if user is None:
            return "OWNER_NOT_INITIALIZED"
        portfolio = await session.scalar(
            select(Portfolio).where(
                Portfolio.user_id == user.id,
                Portfolio.kind == PortfolioKind.PAPER.value,
            )
        )
        instrument = await session.scalar(
            select(InstrumentRecord).where(
                InstrumentRecord.canonical_symbol == instrument_symbol,
                InstrumentRecord.market_type == "linear_perpetual",
            )
        )
        if portfolio is None or instrument is None:
            return "PORTFOLIO_OR_INSTRUMENT_MISSING"
        vector_record = await session.scalar(
            select(FeatureVectorRecord)
            .where(
                FeatureVectorRecord.instrument_id == instrument.id,
                FeatureVectorRecord.horizon == horizon,
            )
            .order_by(FeatureVectorRecord.as_of.desc())
            .limit(1)
        )
        if vector_record is None:
            return "FEATURES_MISSING"
        vector_as_of = _aware(vector_record.as_of)
        book = await session.scalar(
            select(OrderbookSnapshotRecord)
            .where(
                OrderbookSnapshotRecord.instrument_id == instrument.id,
                OrderbookSnapshotRecord.captured_at <= vector_as_of,
            )
            .order_by(OrderbookSnapshotRecord.captured_at.desc())
            .limit(1)
        )
        candle = await session.scalar(
            select(MarketCandleRecord)
            .where(
                MarketCandleRecord.instrument_id == instrument.id,
                MarketCandleRecord.is_closed.is_(True),
                MarketCandleRecord.opened_at <= vector_as_of,
            )
            .order_by(MarketCandleRecord.opened_at.desc())
            .limit(1)
        )
        if book is None or candle is None:
            return "MARKET_SNAPSHOT_MISSING"
        fx = await session.scalar(
            select(FxRateRecord)
            .where(FxRateRecord.base == "USDT", FxRateRecord.quote == "RUB")
            .order_by(FxRateRecord.observed_at.desc())
            .limit(1)
        )
        rate = fx.rate if fx is not None else settings.manual_usdt_rub_rate
        if rate <= 0:
            return "FX_RATE_MISSING"
        portfolio_service = PortfolioService(session)
        balances = await _ensure_usdt_cash(portfolio_service, portfolio.id, rate=rate)
        position_records = list(
            await session.scalars(
                select(PaperPositionRecord).where(
                    PaperPositionRecord.portfolio_id == portfolio.id,
                    PaperPositionRecord.status == "OPEN",
                )
            )
        )
        instrument_position_records = [
            item for item in position_records if item.instrument_id == instrument.id
        ]
        positions = [
            _paper_position(item, instrument_symbol) for item in instrument_position_records
        ]
        market = MarketSnapshot(
            observed_at=_aware(book.captured_at),
            bid=book.best_bid,
            ask=book.best_ask,
            low=candle.low,
            high=candle.high,
        )
        paper_repository = PaperTradingRepository(session)
        signal_service = SignalService(session)
        execution = PaperExecutionEngine(ExecutionSettings())
        metric = await session.scalar(
            select(DerivativeMetricRecord)
            .where(
                DerivativeMetricRecord.instrument_id == instrument.id,
                DerivativeMetricRecord.observed_at <= vector_as_of,
            )
            .order_by(DerivativeMetricRecord.observed_at.desc())
            .limit(1)
        )
        if metric is not None and metric.funding_rate is not None:
            funded_positions: list[PaperPosition] = []
            for record, position in zip(instrument_position_records, positions, strict=True):
                metric_at = _aware(metric.observed_at)
                last_funding_at = (
                    _aware(record.last_funding_at) if record.last_funding_at is not None else None
                )
                if record.instrument_id == instrument.id and (
                    last_funding_at is None or metric_at > last_funding_at
                ):
                    funding = execution.apply_funding(
                        position,
                        funding_rate=metric.funding_rate,
                        mark_price=metric.mark_price or (market.bid + market.ask) / 2,
                    )
                    position = funding.position
                    record.funding_pnl = position.funding_pnl
                    record.last_funding_at = metric_at
                funded_positions.append(position)
            positions = funded_positions
        for position in positions:
            exit_result = execution.evaluate_exit(position, market)
            if exit_result is None:
                continue
            exit_key = f"paper-auto-exit:{position.position_id}:{exit_result.reason.value}"
            closing_order = Order(
                order_id=exit_key,
                symbol=instrument_symbol,
                side=OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=position.quantity,
            )
            reservation = await paper_repository.reserve_order(
                portfolio_id=portfolio.id,
                instrument_id=instrument.id,
                order=closing_order,
                idempotency_key=exit_key,
            )
            if reservation.created:
                candidate = SignalCandidate(
                    instrument=instrument_symbol,
                    market_type=MarketType.LINEAR_PERPETUAL,
                    action=SignalAction.CLOSE,
                    confidence=1.0,
                    reference_price=float((market.bid + market.ask) / 2),
                    horizon=horizon,
                    as_of=market.observed_at,
                    valid_until=market.observed_at + timedelta(minutes=15),
                    model_version=model.version,
                    reasons=(f"Автоматический выход: {exit_result.reason.value}",),
                    cancel_condition="Позиция уже закрыта в paper-контуре",
                )
                signal = await signal_service.record(
                    user_id=user.id,
                    portfolio_id=portfolio.id,
                    instrument_id=instrument.id,
                    candidate=candidate,
                    risk_decision=None,
                )
                await paper_repository.record_close(
                    portfolio_id=portfolio.id,
                    signal_id=signal.id,
                    instrument_id=instrument.id,
                    result=exit_result,
                    reference_price=Decimal(str(candidate.reference_price)),
                    idempotency_key=exit_key,
                    base_asset=instrument.base_asset,
                    quote_asset=instrument.quote_asset,
                )
            return "AUTO_EXIT"

        cash = balances.get("USDT", Decimal("0"))
        midpoint = (market.bid + market.ask) / 2
        equity = _equity(cash, positions, midpoint)
        if equity <= 0:
            return "BANKRUPT"
        today = datetime.combine(now.date(), time.min, tzinfo=UTC)
        points = list(
            await session.scalars(
                select(EquityPointRecord)
                .where(EquityPointRecord.portfolio_id == portfolio.id)
                .order_by(EquityPointRecord.observed_at.desc())
                .limit(10_000)
            )
        )
        day_values = [item.equity_usdt for item in points if _aware(item.observed_at) >= today]
        day_start = day_values[-1] if day_values else equity
        peak = max((item.equity_usdt for item in points), default=equity)
        peak = max(peak, equity)
        session.add(
            EquityPointRecord(
                portfolio_id=portfolio.id,
                observed_at=now,
                equity_usdt=equity,
                equity_rub=equity * rate,
                drawdown_fraction=float(max(Decimal("0"), (peak - equity) / peak)),
            )
        )
        profile = await session.scalar(
            select(RiskProfileRecord).where(RiskProfileRecord.user_id == user.id)
        )
        if profile is None:
            return "RISK_PROFILE_MISSING"
        vector = FeatureVector(
            instrument=instrument_symbol,
            as_of=vector_as_of,
            data_cutoff=_aware(vector_record.data_cutoff),
            names=tuple(vector_record.names),
            values=tuple(float(item) for item in vector_record.values),
        )
        observation = MarketObservation(
            instrument=instrument_symbol,
            as_of=vector.as_of,
            data_cutoff=vector.data_cutoff,
            candles=(
                CandleInput(
                    vector.data_cutoff,
                    float(candle.close),
                    float(candle.high),
                    float(candle.low),
                    float(candle.volume),
                ),
            ),
            bids=((float(book.best_bid), 1.0),),
            asks=((float(book.best_ask), 1.0),),
            aggressive_buy_volume=0.0,
            aggressive_sell_volume=0.0,
            orderbook_at=_aware(book.captured_at),
            trade_flow_at=vector.data_cutoff,
            derivatives_at=vector.data_cutoff,
        )
        worker = TradingWorker(
            feature_pipeline=StoredFeatureBuilder(vector),
            active_model=model,
            signal_policy=SignalPolicy(float(profile.min_signal_confidence)),
            risk_engine=RiskEngine(
                RiskLimits(
                    max_margin_fraction=profile.max_margin_fraction,
                    max_stop_risk_fraction=profile.max_stop_risk_fraction,
                    max_daily_loss_fraction=profile.max_daily_loss_fraction,
                    max_drawdown_fraction=profile.max_drawdown_fraction,
                    max_open_positions=profile.max_open_positions,
                    max_leverage=profile.max_leverage,
                    max_market_age=timedelta(seconds=30),
                )
            ),
            signal_service=signal_service,
            execution_engine=execution,
            paper_repository=paper_repository,
            maximum_observation_age=timedelta(minutes=2),
            execution_costs=ExecutionCosts(
                taker_fee_rate=Decimal("0.00055"),
                slippage_bps=Decimal("2"),
                funding_buffer_rate=Decimal("0.001"),
            ),
            prediction_recorder=DatabasePredictionRecorder(session),
        )
        outcome = await worker.process(
            TradingWorkItem(
                user_id=user.id,
                portfolio_id=portfolio.id,
                instrument_id=instrument.id,
                observation=observation,
                market=market,
                market_type=MarketType.LINEAR_PERPETUAL,
                portfolio=PortfolioRiskState(
                    equity=equity,
                    available_cash=cash,
                    day_start_equity=day_start,
                    peak_equity=peak,
                    open_position_count=len(position_records),
                ),
                base_asset=instrument.base_asset,
                quote_asset=instrument.quote_asset,
                horizon=horizon,
                active_position=positions[0] if positions else None,
            ),
            now=now,
        )
        return outcome.status.value


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    logger = structlog.get_logger()
    try:
        while True:
            try:
                status = await _run_cycle(
                    database,
                    instrument_symbol=args.instrument,
                    horizon=args.horizon,
                    model_dir=args.model_dir,
                )
                logger.info("trading_worker_cycle", status=status)
            except FileNotFoundError:
                logger.warning("trading_worker_waiting_for_champion")
            except Exception:
                logger.exception("trading_worker_cycle_failed")
            if args.once:
                return
            await asyncio.sleep(args.interval_seconds)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-only trading worker")
    parser.add_argument("--instrument", default="BTC/USDT:PERP")
    parser.add_argument("--horizon", default="15m")
    parser.add_argument("--model-dir", type=Path, default=Path("models").resolve())
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
