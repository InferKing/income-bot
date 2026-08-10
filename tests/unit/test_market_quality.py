from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from income_tg.market_data.quality import (
    PriceDivergenceError,
    StaleMarketDataError,
    assert_fresh,
    assert_prices_close,
    find_candle_gaps,
)
from income_tg.market_data.schemas import Candle, DataSource, Instrument


def candle(at: datetime) -> Candle:
    return Candle(
        instrument=Instrument("BTC"),
        interval_seconds=60,
        opened_at=at,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume_base=Decimal("1"),
        turnover_quote=Decimal("1"),
        closed=True,
        source=DataSource.BYBIT,
    )


def test_gap_detection() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    gaps = find_candle_gaps(
        [candle(start), candle(start + timedelta(minutes=2))],
        start,
        start + timedelta(minutes=3),
        60,
    )
    assert [item.expected_at for item in gaps] == [start + timedelta(minutes=1)]


def test_stale_data_is_blocked() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    with pytest.raises(StaleMarketDataError):
        assert_fresh(now - timedelta(seconds=61), maximum_age=timedelta(seconds=60), now=now)


def test_cross_source_price_divergence_is_blocked() -> None:
    with pytest.raises(PriceDivergenceError):
        assert_prices_close(
            Decimal("100"), Decimal("105"), maximum_relative_difference=Decimal("0.01")
        )
    assert_prices_close(
        Decimal("100"), Decimal("100.5"), maximum_relative_difference=Decimal("0.01")
    )
