from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from income_tg.market_data.collector import CollectorPolicy, MarketCollector
from income_tg.market_data.orderbook import OrderBookView
from income_tg.market_data.schemas import (
    Candle,
    DataSource,
    Instrument,
    OrderBookLevel,
    OrderBookUpdate,
    Side,
    Trade,
)


class FakeRepository:
    def __init__(self) -> None:
        self.candles: list[Candle] = []
        self.trades: list[Trade] = []
        self.books: list[OrderBookView] = []
        self.quality: list[str] = []

    async def upsert_candles(self, candles: list[Candle]) -> int:
        self.candles.extend(candles)
        return len(candles)

    async def insert_trade(self, trade: Trade) -> bool:
        self.trades.append(trade)
        return True

    async def insert_orderbook_snapshot(self, update: OrderBookUpdate, view: OrderBookView) -> None:
        del update
        self.books.append(view)

    async def upsert_derivatives_metrics(self, metrics: list[Any]) -> int:
        return len(metrics)

    async def upsert_fx_rate(self, rate: Any) -> bool:
        del rate
        return True

    async def record_quality_event(self, **kwargs: Any) -> object:
        self.quality.append(str(kwargs["event_type"]))
        return object()


def make_collector(repository: FakeRepository) -> MarketCollector:
    return MarketCollector(
        None,  # type: ignore[arg-type]
        repository,
        policy=CollectorPolicy(
            candle_maximum_lag=timedelta(minutes=2),
            trade_maximum_age=timedelta(seconds=30),
            orderbook_maximum_age=timedelta(seconds=30),
        ),
    )


@pytest.mark.asyncio
async def test_stale_trade_is_gated_and_quality_event_is_persisted() -> None:
    repository = FakeRepository()
    collector = make_collector(repository)
    now = datetime.now(UTC)
    trade = Trade(
        instrument=Instrument("BTC"),
        trade_id="old",
        occurred_at=now - timedelta(minutes=1),
        price=Decimal("1"),
        quantity_base=Decimal("1"),
        taker_side=Side.SELL,
        source=DataSource.BYBIT,
    )

    assert not await collector.handle_trade(trade, now=now)
    assert not repository.trades
    assert repository.quality == ["STALE_TRADE"]


@pytest.mark.asyncio
async def test_current_candle_is_persisted() -> None:
    repository = FakeRepository()
    collector = make_collector(repository)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    item = Candle(
        instrument=Instrument("BTC"),
        interval_seconds=60,
        opened_at=now,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume_base=Decimal("1"),
        turnover_quote=Decimal("10"),
        closed=False,
        source=DataSource.BYBIT,
    )

    assert await collector.handle_candle(item, now=now)
    assert repository.candles == [item]


def orderbook_update(sequence: int, *, previous: int | None, snapshot: bool) -> OrderBookUpdate:
    return OrderBookUpdate(
        instrument=Instrument("BTC"),
        occurred_at=datetime.now(UTC),
        bids=(OrderBookLevel(Decimal("100"), Decimal("2")),),
        asks=(OrderBookLevel(Decimal("101"), Decimal("2")),),
        sequence=sequence,
        previous_sequence=previous,
        is_snapshot=snapshot,
        source=DataSource.BYBIT,
    )


@pytest.mark.asyncio
async def test_sequence_gap_is_gated_until_new_snapshot() -> None:
    repository = FakeRepository()
    collector = make_collector(repository)

    assert await collector.handle_orderbook(orderbook_update(10, previous=None, snapshot=True))
    assert not await collector.handle_orderbook(orderbook_update(12, previous=9, snapshot=False))
    assert not await collector.handle_orderbook(orderbook_update(13, previous=12, snapshot=False))
    assert await collector.handle_orderbook(orderbook_update(20, previous=None, snapshot=True))

    assert len(repository.books) == 2
    assert repository.quality == ["ORDERBOOK_SEQUENCE_GAP", "INVALID_ORDERBOOK"]
