from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
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


def _nonnegative(value: Decimal, name: str) -> None:
    _finite(value, name)
    if value < ZERO:
        raise ValueError(f"{name} cannot be negative")


def _rate(value: Decimal, name: str) -> None:
    _finite(value, name)
    if not ZERO <= value < ONE:
        raise ValueError(f"{name} must be in [0, 1)")


class InstrumentKind(StrEnum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"


class PositionSide(StrEnum):
    SPOT = "SPOT"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    FILLED = "FILLED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class Liquidity(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class ExitReason(StrEnum):
    MANUAL = "MANUAL"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    LIQUIDATION = "LIQUIDATION"


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    maker_fee_rate: Decimal = Decimal("0.0002")
    taker_fee_rate: Decimal = Decimal("0.00055")
    slippage_bps: Decimal = Decimal("2")
    maintenance_margin_rate: Decimal = Decimal("0.005")

    def __post_init__(self) -> None:
        _rate(self.maker_fee_rate, "maker_fee_rate")
        _rate(self.taker_fee_rate, "taker_fee_rate")
        _nonnegative(self.slippage_bps, "slippage_bps")
        _rate(self.maintenance_margin_rate, "maintenance_margin_rate")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        _positive(self.bid, "bid")
        _positive(self.ask, "ask")
        _positive(self.low, "low")
        _positive(self.high, "high")
        if self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")
        if self.high < self.low:
            raise ValueError("high cannot be lower than low")


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order_id cannot be blank")
        if not self.symbol.strip():
            raise ValueError("symbol cannot be blank")
        _positive(self.quantity, "quantity")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("market order cannot have limit_price")
        if self.limit_price is not None:
            _positive(self.limit_price, "limit_price")


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    liquidity: Liquidity
    filled_at: datetime

    def __post_init__(self) -> None:
        _positive(self.quantity, "quantity")
        _positive(self.price, "price")
        _nonnegative(self.commission, "commission")

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class OrderExecution:
    status: OrderStatus
    fill: Fill | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is OrderStatus.FILLED and self.fill is None:
            raise ValueError("filled execution requires fill")
        if self.status is not OrderStatus.FILLED and self.fill is not None:
            raise ValueError("non-filled execution cannot contain fill")
        if self.status is OrderStatus.REJECTED and not self.rejection_reason:
            raise ValueError("rejected execution requires a reason")
        if self.status is not OrderStatus.REJECTED and self.rejection_reason is not None:
            raise ValueError("only rejected execution can contain a reason")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    position_id: str
    symbol: str
    instrument: InstrumentKind
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    leverage: int
    margin: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    opening_commission: Decimal
    funding_pnl: Decimal
    opened_at: datetime
    liquidation_price: Decimal | None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id cannot be blank")
        _positive(self.quantity, "quantity")
        _positive(self.entry_price, "entry_price")
        if not 1 <= self.leverage <= 20:
            raise ValueError("leverage must be in [1, 20]")
        _positive(self.margin, "margin")
        _positive(self.stop_loss, "stop_loss")
        _positive(self.take_profit, "take_profit")
        _nonnegative(self.opening_commission, "opening_commission")
        _finite(self.funding_pnl, "funding_pnl")
        if self.liquidation_price is not None:
            _nonnegative(self.liquidation_price, "liquidation_price")

    @property
    def entry_notional(self) -> Decimal:
        return self.entry_price * self.quantity

    def with_funding(self, funding_pnl: Decimal) -> PaperPosition:
        _finite(funding_pnl, "funding_pnl")
        return replace(self, funding_pnl=self.funding_pnl + funding_pnl)


@dataclass(frozen=True, slots=True)
class OpenPositionResult:
    execution: OrderExecution
    position: PaperPosition | None
    cash_delta: Decimal

    def __post_init__(self) -> None:
        _finite(self.cash_delta, "cash_delta")
        if (self.execution.status is OrderStatus.FILLED) != (self.position is not None):
            raise ValueError("filled opening execution and position must appear together")


@dataclass(frozen=True, slots=True)
class FundingResult:
    """Funding accrual that remains unrealized until the position is closed."""

    position: PaperPosition
    funding_pnl: Decimal

    def __post_init__(self) -> None:
        _finite(self.funding_pnl, "funding_pnl")

    @property
    def cash_delta(self) -> Decimal:
        """Funding is accrued on the position and must not be posted to cash yet."""

        return ZERO


@dataclass(frozen=True, slots=True)
class CloseResult:
    position: PaperPosition
    fill: Fill
    reason: ExitReason
    gross_pnl: Decimal
    net_pnl: Decimal
    cash_delta: Decimal

    def __post_init__(self) -> None:
        _finite(self.gross_pnl, "gross_pnl")
        _finite(self.net_pnl, "net_pnl")
        _finite(self.cash_delta, "cash_delta")
        if self.fill.quantity != self.position.quantity:
            raise ValueError("close must fill the full position quantity")
