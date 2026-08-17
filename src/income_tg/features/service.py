from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.features.pipeline import CandleInput, FeaturePipeline, MarketObservation
from income_tg.features.repository import FeatureRepository, feature_schema_hash
from income_tg.market_data.quality import MarketDataQualityError, assert_prices_close
from income_tg.storage.trading_models import (
    DerivativeMetricRecord,
    FeatureVectorRecord,
    InstrumentRecord,
    MarketCandleRecord,
    MarketTradeRecord,
    OrderbookSnapshotRecord,
)


@dataclass(frozen=True, slots=True)
class FeatureBuildResult:
    vectors_created: int
    outcome: str
    as_of: datetime | None = None
    candidate_lag_seconds: int | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class OnlineFeatureService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build_latest(
        self,
        instrument: InstrumentRecord,
        *,
        horizons: tuple[str, ...] = ("5m", "15m", "1h", "4h"),
    ) -> FeatureBuildResult:
        candles = list(
            await self.session.scalars(
                select(MarketCandleRecord)
                .where(
                    MarketCandleRecord.instrument_id == instrument.id,
                    MarketCandleRecord.provider == "bybit",
                    MarketCandleRecord.interval_seconds == 60,
                    MarketCandleRecord.is_closed.is_(True),
                )
                .order_by(MarketCandleRecord.opened_at.desc())
                .limit(30)
            )
        )
        candles.reverse()
        if len(candles) < 20:
            return FeatureBuildResult(0, "insufficient_primary_candles")

        latest_as_of = _as_utc(candles[-1].opened_at) + timedelta(
            seconds=candles[-1].interval_seconds
        )
        reserve_candles = list(
            await self.session.scalars(
                select(MarketCandleRecord).where(
                    MarketCandleRecord.instrument_id == instrument.id,
                    MarketCandleRecord.provider == "okx",
                    MarketCandleRecord.interval_seconds == 60,
                    MarketCandleRecord.opened_at.in_([item.opened_at for item in candles]),
                    MarketCandleRecord.is_closed.is_(True),
                )
            )
        )
        reserve_by_opened_at = {item.opened_at: item for item in reserve_candles}
        candidate: tuple[int, MarketCandleRecord, OrderbookSnapshotRecord] | None = None
        saw_reserve = False
        saw_matching_price = False
        for index in range(len(candles) - 1, 18, -1):
            primary = candles[index]
            reserve = reserve_by_opened_at.get(primary.opened_at)
            if reserve is None:
                continue
            saw_reserve = True
            try:
                assert_prices_close(
                    primary.close,
                    reserve.close,
                    maximum_relative_difference=Decimal("0.01"),
                )
            except MarketDataQualityError:
                continue
            saw_matching_price = True
            candidate_as_of = _as_utc(primary.opened_at) + timedelta(
                seconds=primary.interval_seconds
            )
            book = await self.session.scalar(
                select(OrderbookSnapshotRecord)
                .where(
                    OrderbookSnapshotRecord.instrument_id == instrument.id,
                    OrderbookSnapshotRecord.provider == "bybit",
                    OrderbookSnapshotRecord.captured_at > candidate_as_of - timedelta(seconds=30),
                    OrderbookSnapshotRecord.captured_at <= candidate_as_of,
                )
                .order_by(OrderbookSnapshotRecord.captured_at.desc())
                .limit(1)
            )
            if book is not None:
                candidate = (index, primary, book)
                break

        if candidate is None:
            if not saw_reserve:
                outcome = "reserve_candle_missing"
            elif not saw_matching_price:
                outcome = "price_divergence"
            else:
                outcome = "fresh_orderbook_missing"
            return FeatureBuildResult(0, outcome, latest_as_of)

        index, latest, book = candidate
        as_of = _as_utc(latest.opened_at) + timedelta(seconds=latest.interval_seconds)
        candle_window = candles[max(0, index - 29) : index + 1]
        metric = await self.session.scalar(
            select(DerivativeMetricRecord)
            .where(
                DerivativeMetricRecord.instrument_id == instrument.id,
                DerivativeMetricRecord.provider == "bybit",
                DerivativeMetricRecord.observed_at <= as_of,
            )
            .order_by(DerivativeMetricRecord.observed_at.desc())
            .limit(1)
        )
        previous_metric = None
        if metric is not None:
            previous_metric = await self.session.scalar(
                select(DerivativeMetricRecord)
                .where(
                    DerivativeMetricRecord.instrument_id == instrument.id,
                    DerivativeMetricRecord.provider == "bybit",
                    DerivativeMetricRecord.observed_at < metric.observed_at,
                )
                .order_by(DerivativeMetricRecord.observed_at.desc())
                .limit(1)
            )
        recent_trades = list(
            await self.session.scalars(
                select(MarketTradeRecord).where(
                    MarketTradeRecord.instrument_id == instrument.id,
                    MarketTradeRecord.provider == "bybit",
                    MarketTradeRecord.occurred_at > as_of - timedelta(minutes=1),
                    MarketTradeRecord.occurred_at <= as_of,
                )
            )
        )
        buy_volume = sum(
            float(item.quantity) for item in recent_trades if item.side.casefold() == "buy"
        )
        sell_volume = sum(
            float(item.quantity) for item in recent_trades if item.side.casefold() == "sell"
        )
        close = float(latest.close)
        vector = FeaturePipeline().build(
            MarketObservation(
                instrument=instrument.canonical_symbol,
                as_of=as_of,
                data_cutoff=as_of,
                candles=tuple(
                    CandleInput(
                        close_time=_as_utc(item.opened_at)
                        + timedelta(seconds=item.interval_seconds),
                        close=float(item.close),
                        high=float(item.high),
                        low=float(item.low),
                        volume=float(item.volume),
                    )
                    for item in candle_window
                ),
                bids=tuple((float(price), float(quantity)) for price, quantity in book.bids),
                asks=tuple((float(price), float(quantity)) for price, quantity in book.asks),
                aggressive_buy_volume=buy_volume,
                aggressive_sell_volume=sell_volume,
                orderbook_at=_as_utc(book.captured_at),
                trade_flow_at=max(
                    (_as_utc(item.occurred_at) for item in recent_trades),
                    default=as_of,
                ),
                derivatives_at=_as_utc(metric.observed_at) if metric else as_of,
                funding_rate=float(metric.funding_rate or 0) if metric else 0.0,
                open_interest=float(metric.open_interest or 0) if metric else 0.0,
                previous_open_interest=(
                    float(previous_metric.open_interest or 0) if previous_metric else 0.0
                ),
                mark_price=float(metric.mark_price or close) if metric else close,
                index_price=float(metric.index_price or close) if metric else close,
                long_liquidations=0.0,
                short_liquidations=0.0,
            )
        )
        repository = FeatureRepository(self.session)
        schema_hash = feature_schema_hash(vector.names)
        existing_horizons = set(
            await self.session.scalars(
                select(FeatureVectorRecord.horizon).where(
                    FeatureVectorRecord.instrument_id == instrument.id,
                    FeatureVectorRecord.as_of == vector.as_of,
                    FeatureVectorRecord.schema_hash == schema_hash,
                    FeatureVectorRecord.horizon.in_(horizons),
                )
            )
        )
        created = 0
        for horizon in horizons:
            if horizon not in existing_horizons:
                await repository.save(instrument_id=instrument.id, horizon=horizon, vector=vector)
                created += 1
        lag = max(0, round((latest_as_of - as_of).total_seconds()))
        return FeatureBuildResult(
            created,
            "created" if created else "up_to_date",
            as_of,
            lag,
        )
