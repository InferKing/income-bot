"""Shared normalization helpers for exchange adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from income_tg.market_data.schemas import Instrument, InstrumentKind, OrderBookLevel

_INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}


def interval_seconds(interval: str) -> int:
    try:
        return _INTERVAL_SECONDS[interval.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc


def bybit_interval(interval: str) -> str:
    seconds = interval_seconds(interval)
    if seconds < 3600:
        return str(seconds // 60)
    if seconds < 86400:
        return str(seconds // 3600 * 60)
    return "D"


def okx_interval(interval: str) -> str:
    seconds = interval_seconds(interval)
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}H"
    return "1Dutc"


def decimal_value(value: Any, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal in {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal in {field}")
    return result


def optional_decimal(value: Any, *, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    return decimal_value(value, field=field)


def utc_from_milliseconds(value: Any) -> datetime:
    milliseconds = int(value)
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def levels(values: list[list[str]] | tuple[tuple[str, str], ...]) -> tuple[OrderBookLevel, ...]:
    return tuple(
        OrderBookLevel(
            price=decimal_value(item[0], field="orderbook.price"),
            quantity_base=decimal_value(item[1], field="orderbook.quantity"),
        )
        for item in values
    )


def bybit_symbol(instrument: Instrument) -> str:
    return f"{instrument.base}{instrument.quote}"


def okx_symbol(instrument: Instrument) -> str:
    suffix = "-SWAP" if instrument.kind is InstrumentKind.LINEAR_PERPETUAL else ""
    return f"{instrument.base}-{instrument.quote}{suffix}"
