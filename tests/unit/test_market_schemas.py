from datetime import UTC, datetime
from decimal import Decimal

import pytest

from income_tg.market_data.schemas import Candle, DataSource, Instrument, Side, Trade


def test_candle_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="OHLC"):
        Candle(
            Instrument("BTC"),
            60,
            datetime.now(UTC),
            Decimal("10"),
            Decimal("9"),
            Decimal("8"),
            Decimal("10"),
            Decimal("1"),
            None,
            True,
            DataSource.BYBIT,
        )


def test_trade_rejects_naive_timestamp_and_nonpositive_quantity() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Trade(
            Instrument("BTC"),
            "t1",
            datetime(2026, 1, 1),
            Decimal("10"),
            Decimal("1"),
            Side.BUY,
            DataSource.BYBIT,
        )
    with pytest.raises(ValueError, match="positive"):
        Trade(
            Instrument("BTC"),
            "t2",
            datetime.now(UTC),
            Decimal("10"),
            Decimal("0"),
            Side.BUY,
            DataSource.BYBIT,
        )
