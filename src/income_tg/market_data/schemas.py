"""Exchange-independent market-data values.

All prices and quantities are ``Decimal`` and all timestamps are timezone-aware
UTC values.  Adapters must construct these types at their boundary so exchange
specific units never leak into downstream trading code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class DataSource(StrEnum):
    BYBIT = "bybit"
    OKX = "okx"


class InstrumentKind(StrEnum):
    SPOT = "spot"
    LINEAR_PERPETUAL = "linear_perpetual"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def _finite(value: Decimal, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _positive(value: Decimal, field: str) -> None:
    _finite(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _nonnegative(value: Decimal, field: str) -> None:
    _finite(value, field)
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


@dataclass(frozen=True, slots=True)
class Instrument:
    base: str
    quote: str = "USDT"
    kind: InstrumentKind = InstrumentKind.SPOT

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", self.base.upper())
        object.__setattr__(self, "quote", self.quote.upper())
        if not self.base or not self.quote:
            raise ValueError("instrument base and quote must not be empty")

    @property
    def symbol(self) -> str:
        suffix = ":PERP" if self.kind is InstrumentKind.LINEAR_PERPETUAL else ""
        return f"{self.base}/{self.quote}{suffix}"


@dataclass(frozen=True, slots=True)
class Candle:
    instrument: Instrument
    interval_seconds: int
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_base: Decimal
    turnover_quote: Decimal | None
    closed: bool
    source: DataSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "opened_at", _utc(self.opened_at, "opened_at"))
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        for name in ("open", "high", "low", "close"):
            _positive(getattr(self, name), name)
        _nonnegative(self.volume_base, "volume_base")
        if self.turnover_quote is not None:
            _nonnegative(self.turnover_quote, "turnover_quote")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")

    @property
    def identity(self) -> tuple[str, int, datetime]:
        return (self.instrument.symbol, self.interval_seconds, self.opened_at)


@dataclass(frozen=True, slots=True)
class Trade:
    instrument: Instrument
    trade_id: str
    occurred_at: datetime
    price: Decimal
    quantity_base: Decimal
    taker_side: Side
    source: DataSource

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise ValueError("trade_id must not be empty")
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        _positive(self.price, "price")
        _positive(self.quantity_base, "quantity_base")


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity_base: Decimal

    def __post_init__(self) -> None:
        _positive(self.price, "price")
        # Zero quantities are valid deletion markers in incremental books.
        _nonnegative(self.quantity_base, "quantity_base")


@dataclass(frozen=True, slots=True)
class OrderBookUpdate:
    instrument: Instrument
    occurred_at: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int
    previous_sequence: int | None
    is_snapshot: bool
    source: DataSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if self.sequence < 0 or (self.previous_sequence is not None and self.previous_sequence < 0):
            raise ValueError("order-book sequences must be non-negative")


@dataclass(frozen=True, slots=True)
class DerivativesMetrics:
    instrument: Instrument
    occurred_at: datetime
    open_interest_base: Decimal | None
    funding_rate: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    source: DataSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if self.open_interest_base is not None:
            _nonnegative(self.open_interest_base, "open_interest_base")
        if self.funding_rate is not None:
            _finite(self.funding_rate, "funding_rate")
        if self.mark_price is not None:
            _positive(self.mark_price, "mark_price")
        if self.index_price is not None:
            _positive(self.index_price, "index_price")


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    instrument: Instrument
    price_tick: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal | None
    maker_fee_rate: Decimal | None
    taker_fee_rate: Decimal | None
    source: DataSource

    def __post_init__(self) -> None:
        _positive(self.price_tick, "price_tick")
        _positive(self.quantity_step, "quantity_step")
        _positive(self.minimum_quantity, "minimum_quantity")
        if self.minimum_notional is not None:
            _positive(self.minimum_notional, "minimum_notional")
        for name in ("maker_fee_rate", "taker_fee_rate"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    source: DataSource
    healthy: bool
    checked_at: datetime
    latency_ms: int
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "checked_at", _utc(self.checked_at, "checked_at"))
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", self.base.upper())
        object.__setattr__(self, "quote", self.quote.upper())
        if not self.base or not self.quote or not self.source:
            raise ValueError("FX symbols and source must not be empty")
        _positive(self.rate, "rate")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
