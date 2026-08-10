from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.features.pipeline import CandleInput, FeaturePipeline, MarketObservation
from income_tg.features.repository import FeatureRepository
from income_tg.market_data.quality import MarketDataQualityError, assert_prices_close
from income_tg.storage.trading_models import (
    DerivativeMetricRecord,
    InstrumentRecord,
    MarketCandleRecord,
    MarketTradeRecord,
    OrderbookSnapshotRecord,
)


class OnlineFeatureService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build_latest(
        self,
        instrument: InstrumentRecord,
        *,
        horizons: tuple[str, ...] = ("5m", "15m", "1h", "4h"),
    ) -> int:
        candles = list(
            await self.session.scalars(
                select(MarketCandleRecord)
                .where(
                    MarketCandleRecord.instrument_id == instrument.id,
                    MarketCandleRecord.provider == "bybit",
                    MarketCandleRecord.is_closed.is_(True),
                )
                .order_by(MarketCandleRecord.opened_at.desc())
                .limit(30)
            )
        )
        candles.reverse()
        if len(candles) < 20:
            return 0
        latest = candles[-1]
        as_of = latest.opened_at + timedelta(seconds=latest.interval_seconds)
        reserve_candle = await self.session.scalar(
            select(MarketCandleRecord).where(
                MarketCandleRecord.instrument_id == instrument.id,
                MarketCandleRecord.provider == "okx",
                MarketCandleRecord.interval_seconds == latest.interval_seconds,
                MarketCandleRecord.opened_at == latest.opened_at,
                MarketCandleRecord.is_closed.is_(True),
            )
        )
        if reserve_candle is None:
            return 0
        try:
            assert_prices_close(
                latest.close,
                reserve_candle.close,
                maximum_relative_difference=Decimal("0.01"),
            )
        except MarketDataQualityError:
            return 0
        book = await self.session.scalar(
            select(OrderbookSnapshotRecord)
            .where(
                OrderbookSnapshotRecord.instrument_id == instrument.id,
                OrderbookSnapshotRecord.provider == "bybit",
                OrderbookSnapshotRecord.captured_at <= as_of,
            )
            .order_by(OrderbookSnapshotRecord.captured_at.desc())
            .limit(1)
        )
        if book is None:
            return 0
        if as_of - book.captured_at > timedelta(seconds=30):
            return 0
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
                        close_time=item.opened_at + timedelta(seconds=item.interval_seconds),
                        close=float(item.close),
                        high=float(item.high),
                        low=float(item.low),
                        volume=float(item.volume),
                    )
                    for item in candles
                ),
                bids=tuple((float(price), float(quantity)) for price, quantity in book.bids),
                asks=tuple((float(price), float(quantity)) for price, quantity in book.asks),
                aggressive_buy_volume=buy_volume,
                aggressive_sell_volume=sell_volume,
                orderbook_at=book.captured_at,
                trade_flow_at=max(
                    (item.occurred_at for item in recent_trades),
                    default=as_of,
                ),
                derivatives_at=metric.observed_at if metric else as_of,
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
        for horizon in horizons:
            await repository.save(instrument_id=instrument.id, horizon=horizon, vector=vector)
        return len(horizons)
