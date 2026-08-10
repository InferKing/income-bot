from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class BacktestValidationError(ValueError):
    """Raised when market data or a strategy decision violates an invariant."""


@dataclass(frozen=True, slots=True)
class Bar:
    """A completed OHLCV bar. ``timestamp`` is the bar close time."""

    timestamp: datetime
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise BacktestValidationError("bar timestamp must be timezone-aware")
        if not self.symbol.strip():
            raise BacktestValidationError("bar symbol must not be empty")
        prices = (self.open, self.high, self.low, self.close)
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise BacktestValidationError("bar prices must be finite and positive")
        if self.volume < 0 or not self.volume.is_finite():
            raise BacktestValidationError("bar volume must be finite and non-negative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise BacktestValidationError("bar high/low do not contain open and close")
        if self.low > self.high:
            raise BacktestValidationError("bar low must not exceed high")


@dataclass(frozen=True, slots=True)
class TargetSignal:
    """Desired signed notional exposure, generated using data available at ``as_of``."""

    target_exposure: Decimal
    as_of: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.target_exposure.is_finite():
            raise BacktestValidationError("target exposure must be finite")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise BacktestValidationError("signal as_of must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Immutable point-in-time view shared by backtest and live-paper strategies."""

    symbol: str
    as_of: datetime
    bars: tuple[Bar, ...]
    cash: Decimal
    equity: Decimal
    quantity: Decimal
    current_target: Decimal

    def __post_init__(self) -> None:
        if not self.bars:
            raise BacktestValidationError("strategy context requires at least one bar")
        previous: datetime | None = None
        for bar in self.bars:
            if bar.symbol != self.symbol:
                raise BacktestValidationError("context bars must have one symbol")
            if bar.timestamp > self.as_of:
                raise BacktestValidationError("context contains data newer than as_of")
            if previous is not None and bar.timestamp <= previous:
                raise BacktestValidationError("context bars must be strictly chronological")
            previous = bar.timestamp
        if self.bars[-1].timestamp != self.as_of:
            raise BacktestValidationError("latest context bar must end at as_of")


class Strategy(Protocol):
    """Deterministic strategy contract used by both historical and live-paper runners."""

    def reset(self) -> None:
        """Reset mutable state before a new independent run."""

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        """Return a target after observing a completed bar, or no change."""


class ExecutionSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Execution:
    timestamp: datetime
    side: ExecutionSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    entry_at: datetime
    exit_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    net_return: float
    max_drawdown: float
    profit_factor: float
    expectancy: float
    sharpe: float
    sortino: float
    trade_count: int
    total_fees: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    initial_cash: Decimal
    final_equity: Decimal
    executions: tuple[Execution, ...]
    trades: tuple[ClosedTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: PerformanceMetrics
