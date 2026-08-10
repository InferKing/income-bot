from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")
ONE = Decimal("1")


def _finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _positive(value: Decimal, name: str) -> None:
    _finite(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _fraction(value: Decimal, name: str, *, allow_zero: bool = False) -> None:
    _finite(value, name)
    lower_bound_ok = value >= ZERO if allow_zero else value > ZERO
    if not lower_bound_ok or value > ONE:
        qualifier = "[0, 1]" if allow_zero else "(0, 1]"
        raise ValueError(f"{name} must be in {qualifier}")


class PositionDirection(StrEnum):
    SPOT = "SPOT"
    LONG = "LONG"
    SHORT = "SHORT"


class RejectionReason(StrEnum):
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    FUTURE_MARKET_DATA = "FUTURE_MARKET_DATA"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    OPEN_POSITION_LIMIT = "OPEN_POSITION_LIMIT"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INVALID_STOP = "INVALID_STOP"
    BELOW_MIN_QUANTITY = "BELOW_MIN_QUANTITY"
    BELOW_MIN_NOTIONAL = "BELOW_MIN_NOTIONAL"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_margin_fraction: Decimal = Decimal("0.10")
    max_stop_risk_fraction: Decimal = Decimal("0.01")
    max_daily_loss_fraction: Decimal = Decimal("0.05")
    max_drawdown_fraction: Decimal = Decimal("0.15")
    max_open_positions: int = 3
    max_leverage: int = 20
    max_market_age: timedelta = timedelta(seconds=5)
    max_future_skew: timedelta = timedelta(seconds=1)
    max_spread_bps: Decimal = Decimal("20")
    min_notional: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        _fraction(self.max_margin_fraction, "max_margin_fraction")
        _fraction(self.max_stop_risk_fraction, "max_stop_risk_fraction")
        _fraction(self.max_daily_loss_fraction, "max_daily_loss_fraction")
        _fraction(self.max_drawdown_fraction, "max_drawdown_fraction")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be positive")
        if not 1 <= self.max_leverage <= 20:
            raise ValueError("max_leverage must be in [1, 20]")
        if self.max_market_age < timedelta(0):
            raise ValueError("max_market_age cannot be negative")
        if self.max_future_skew < timedelta(0):
            raise ValueError("max_future_skew cannot be negative")
        _finite(self.max_spread_bps, "max_spread_bps")
        if self.max_spread_bps < ZERO:
            raise ValueError("max_spread_bps cannot be negative")
        _positive(self.min_notional, "min_notional")


@dataclass(frozen=True, slots=True)
class MarketGuard:
    observed_at: datetime
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        _positive(self.bid, "bid")
        _positive(self.ask, "ask")
        if self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask - self.bid) / self.midpoint * Decimal("10000")


@dataclass(frozen=True, slots=True)
class ExecutionCosts:
    """Conservative round-trip cost assumptions used by the risk gate."""

    taker_fee_rate: Decimal = ZERO
    slippage_bps: Decimal = ZERO
    funding_buffer_rate: Decimal = ZERO

    def __post_init__(self) -> None:
        _fraction(self.taker_fee_rate, "taker_fee_rate", allow_zero=True)
        _finite(self.slippage_bps, "slippage_bps")
        if not ZERO <= self.slippage_bps <= Decimal("10000"):
            raise ValueError("slippage_bps must be in [0, 10000]")
        _fraction(self.funding_buffer_rate, "funding_buffer_rate", allow_zero=True)


@dataclass(frozen=True, slots=True)
class VenueConstraints:
    quantity_step: Decimal | None = None
    minimum_quantity: Decimal | None = None
    minimum_notional: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity_step is not None:
            _positive(self.quantity_step, "quantity_step")
        if self.minimum_quantity is not None:
            _positive(self.minimum_quantity, "minimum_quantity")
        if self.minimum_notional is not None:
            _positive(self.minimum_notional, "minimum_notional")


@dataclass(frozen=True, slots=True)
class PortfolioRiskState:
    equity: Decimal
    available_cash: Decimal
    day_start_equity: Decimal
    peak_equity: Decimal
    open_position_count: int

    def __post_init__(self) -> None:
        _positive(self.equity, "equity")
        _finite(self.available_cash, "available_cash")
        if self.available_cash < ZERO:
            raise ValueError("available_cash cannot be negative")
        _positive(self.day_start_equity, "day_start_equity")
        _positive(self.peak_equity, "peak_equity")
        if self.open_position_count < 0:
            raise ValueError("open_position_count cannot be negative")

    @property
    def daily_loss_fraction(self) -> Decimal:
        return max(ZERO, (self.day_start_equity - self.equity) / self.day_start_equity)

    @property
    def drawdown_fraction(self) -> Decimal:
        return max(ZERO, (self.peak_equity - self.equity) / self.peak_equity)


@dataclass(frozen=True, slots=True)
class SizingRequest:
    direction: PositionDirection
    entry_price: Decimal
    stop_price: Decimal
    market: MarketGuard
    portfolio: PortfolioRiskState
    requested_risk_fraction: Decimal | None = None
    execution_costs: ExecutionCosts = ExecutionCosts()
    venue: VenueConstraints = VenueConstraints()

    def __post_init__(self) -> None:
        _positive(self.entry_price, "entry_price")
        _positive(self.stop_price, "stop_price")
        if self.requested_risk_fraction is not None:
            _fraction(self.requested_risk_fraction, "requested_risk_fraction")


@dataclass(frozen=True, slots=True)
class SizingResult:
    quantity: Decimal
    notional: Decimal
    margin: Decimal
    leverage: int
    stop_loss_amount: Decimal
    stop_distance_fraction: Decimal
    effective_entry_price: Decimal | None = None
    price_loss_amount: Decimal | None = None
    cost_buffer_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        _positive(self.quantity, "quantity")
        _positive(self.notional, "notional")
        _positive(self.margin, "margin")
        if not 1 <= self.leverage <= 20:
            raise ValueError("leverage must be in [1, 20]")
        _positive(self.stop_loss_amount, "stop_loss_amount")
        _positive(self.stop_distance_fraction, "stop_distance_fraction")
        if self.effective_entry_price is not None:
            _positive(self.effective_entry_price, "effective_entry_price")
        if self.price_loss_amount is not None:
            _positive(self.price_loss_amount, "price_loss_amount")
        _finite(self.cost_buffer_amount, "cost_buffer_amount")
        if self.cost_buffer_amount < ZERO:
            raise ValueError("cost_buffer_amount cannot be negative")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    sizing: SizingResult | None
    reasons: tuple[RejectionReason, ...] = ()

    def __post_init__(self) -> None:
        if (self.sizing is None) == (not self.reasons):
            raise ValueError("decision must contain either sizing or rejection reasons")

    @property
    def approved(self) -> bool:
        return self.sizing is not None
