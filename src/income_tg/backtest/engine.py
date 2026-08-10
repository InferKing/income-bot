from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from income_tg.backtest.metrics import calculate_metrics
from income_tg.backtest.models import (
    BacktestResult,
    BacktestValidationError,
    Bar,
    ClosedTrade,
    EquityPoint,
    Execution,
    ExecutionSide,
    Strategy,
    StrategyContext,
    TargetSignal,
)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal = Decimal("100000")
    fee_rate: Decimal = Decimal("0.001")
    slippage_bps: Decimal = Decimal("5")
    max_abs_exposure: Decimal = Decimal("1")
    periods_per_year: int = 365 * 24
    liquidate_at_end: bool = True

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or not self.initial_cash.is_finite():
            raise ValueError("initial cash must be finite and positive")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise ValueError("fee rate must be in [0, 1)")
        if self.slippage_bps < 0 or not self.slippage_bps.is_finite():
            raise ValueError("slippage must be finite and non-negative")
        if self.max_abs_exposure <= 0 or not self.max_abs_exposure.is_finite():
            raise ValueError("maximum exposure must be finite and positive")
        if self.max_abs_exposure > Decimal("20"):
            raise ValueError("maximum exposure cannot exceed 20x")
        if self.periods_per_year <= 0:
            raise ValueError("periods per year must be positive")


@dataclass(slots=True)
class _OpenTrade:
    entry_at: datetime
    quantity: Decimal
    entry_price: Decimal
    entry_fee: Decimal


class BacktestEngine:
    """Close-observe/next-open-execute event loop with deterministic fill assumptions."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        bars: Sequence[Bar],
        strategy: Strategy,
        *,
        history: Sequence[Bar] = (),
    ) -> BacktestResult:
        market = tuple(bars)
        symbol = self._validate_market(market)
        prior_bars = self._validate_history(tuple(history), symbol, market[0])
        strategy.reset()
        cash = self.config.initial_cash
        quantity = Decimal("0")
        target = Decimal("0")
        pending: TargetSignal | None = None
        open_trade: _OpenTrade | None = None
        executions: list[Execution] = []
        trades: list[ClosedTrade] = []
        curve: list[EquityPoint] = []
        total_fees = Decimal("0")

        if prior_bars:
            prior_context = StrategyContext(
                symbol=symbol,
                as_of=prior_bars[-1].timestamp,
                bars=prior_bars,
                cash=cash,
                equity=cash,
                quantity=quantity,
                current_target=target,
            )
            pending = strategy.on_bar(prior_context)
            if pending is not None:
                self._validate_signal(pending, prior_context)

        for index, bar in enumerate(market):
            if pending is not None and pending.target_exposure != target:
                cash, quantity, open_trade, fee = self._transition(
                    bar,
                    pending,
                    cash,
                    quantity,
                    open_trade,
                    executions,
                    trades,
                )
                total_fees += fee
                target = pending.target_exposure
            pending = None

            if quantity == 0 and cash <= 0:
                cash = Decimal("0")
                quantity = Decimal("0")
                open_trade = None
                target = Decimal("0")
                curve.extend(EquityPoint(item.timestamp, cash) for item in market[index:])
                break

            liquidation_reference = self._liquidation_reference(cash, quantity, bar)
            if liquidation_reference is not None:
                cash, quantity, open_trade, fee = self._liquidate(
                    bar,
                    liquidation_reference,
                    cash,
                    quantity,
                    open_trade,
                    executions,
                    trades,
                )
                total_fees += fee
                target = Decimal("0")
                curve.extend(EquityPoint(item.timestamp, cash) for item in market[index:])
                break

            equity = cash + quantity * bar.close
            curve.append(EquityPoint(bar.timestamp, equity))
            context = StrategyContext(
                symbol=symbol,
                as_of=bar.timestamp,
                bars=prior_bars + market[: index + 1],
                cash=cash,
                equity=equity,
                quantity=quantity,
                current_target=target,
            )
            decision = strategy.on_bar(context)
            if decision is not None:
                self._validate_signal(decision, context)
                pending = decision

        if self.config.liquidate_at_end and quantity != 0:
            final_bar = market[-1]
            closing = TargetSignal(Decimal("0"), final_bar.timestamp, "end of backtest")
            cash, quantity, open_trade, fee = self._transition(
                final_bar,
                closing,
                cash,
                quantity,
                open_trade,
                executions,
                trades,
                at_close=True,
            )
            total_fees += fee
            curve[-1] = EquityPoint(final_bar.timestamp, cash)

        final_equity = curve[-1].equity
        metrics = calculate_metrics(
            self.config.initial_cash,
            curve,
            trades,
            total_fees,
            self.config.periods_per_year,
        )
        return BacktestResult(
            symbol=symbol,
            initial_cash=self.config.initial_cash,
            final_equity=final_equity,
            executions=tuple(executions),
            trades=tuple(trades),
            equity_curve=tuple(curve),
            metrics=metrics,
        )

    def _transition(
        self,
        bar: Bar,
        signal: TargetSignal,
        cash: Decimal,
        quantity: Decimal,
        open_trade: _OpenTrade | None,
        executions: list[Execution],
        trades: list[ClosedTrade],
        *,
        at_close: bool = False,
    ) -> tuple[Decimal, Decimal, _OpenTrade | None, Decimal]:
        reference_price = bar.close if at_close else bar.open
        fee_total = Decimal("0")
        if quantity != 0:
            side = ExecutionSide.SELL if quantity > 0 else ExecutionSide.BUY
            close_quantity = abs(quantity)
            price = self._fill_price(reference_price, side)
            fee = close_quantity * price * self.config.fee_rate
            cash -= -quantity * price + fee
            fee_total += fee
            executions.append(
                Execution(bar.timestamp, side, close_quantity, price, fee, signal.reason)
            )
            if open_trade is None:
                raise RuntimeError("position exists without an open trade")
            pnl = quantity * (price - open_trade.entry_price) - open_trade.entry_fee - fee
            trades.append(
                ClosedTrade(
                    open_trade.entry_at,
                    bar.timestamp,
                    open_trade.quantity,
                    open_trade.entry_price,
                    price,
                    pnl,
                )
            )
            quantity = Decimal("0")
            open_trade = None

        if signal.target_exposure != 0 and cash > 0:
            equity = cash
            side = ExecutionSide.BUY if signal.target_exposure > 0 else ExecutionSide.SELL
            price = self._fill_price(reference_price, side)
            notional = equity * abs(signal.target_exposure) / (Decimal("1") + self.config.fee_rate)
            absolute_quantity = notional / price
            signed_quantity = absolute_quantity if side is ExecutionSide.BUY else -absolute_quantity
            fee = notional * self.config.fee_rate
            cash -= signed_quantity * price + fee
            fee_total += fee
            quantity = signed_quantity
            open_trade = _OpenTrade(bar.timestamp, quantity, price, fee)
            executions.append(
                Execution(bar.timestamp, side, absolute_quantity, price, fee, signal.reason)
            )
        return cash, quantity, open_trade, fee_total

    def _liquidation_reference(
        self,
        cash: Decimal,
        quantity: Decimal,
        bar: Bar,
    ) -> Decimal | None:
        """Return the bankruptcy price crossed by this bar, including exit fees."""

        if quantity > 0 and cash < 0:
            proceeds_multiplier = Decimal("1") - self.config.fee_rate
            threshold = -cash / (quantity * proceeds_multiplier)
            if bar.low <= threshold:
                return min(threshold, bar.open)
        elif quantity < 0 and cash > 0:
            cost_multiplier = Decimal("1") + self.config.fee_rate
            threshold = cash / (-quantity * cost_multiplier)
            if bar.high >= threshold:
                return max(threshold, bar.open)
        return None

    def _liquidate(
        self,
        bar: Bar,
        reference_price: Decimal,
        cash: Decimal,
        quantity: Decimal,
        open_trade: _OpenTrade | None,
        executions: list[Execution],
        trades: list[ClosedTrade],
    ) -> tuple[Decimal, Decimal, None, Decimal]:
        if open_trade is None or quantity == 0:
            raise RuntimeError("liquidation requires an open trade")
        side = ExecutionSide.SELL if quantity > 0 else ExecutionSide.BUY
        absolute_quantity = abs(quantity)
        price = self._fill_price(reference_price, side)
        fee = absolute_quantity * price * self.config.fee_rate
        cash -= -quantity * price + fee
        executions.append(
            Execution(
                bar.timestamp,
                side,
                absolute_quantity,
                price,
                fee,
                "bankruptcy/liquidation",
            )
        )
        pnl = quantity * (price - open_trade.entry_price) - open_trade.entry_fee - fee
        trades.append(
            ClosedTrade(
                open_trade.entry_at,
                bar.timestamp,
                open_trade.quantity,
                open_trade.entry_price,
                price,
                pnl,
            )
        )
        return max(Decimal("0"), cash), Decimal("0"), None, fee

    def _fill_price(self, reference: Decimal, side: ExecutionSide) -> Decimal:
        adjustment = self.config.slippage_bps / Decimal("10000")
        if side is ExecutionSide.BUY:
            return reference * (Decimal("1") + adjustment)
        return reference * (Decimal("1") - adjustment)

    def _validate_signal(self, signal: TargetSignal, context: StrategyContext) -> None:
        if signal.as_of != context.as_of:
            raise BacktestValidationError("signal as_of must equal the current completed bar")
        if abs(signal.target_exposure) > self.config.max_abs_exposure:
            raise BacktestValidationError("signal exceeds configured maximum exposure")

    @staticmethod
    def _validate_market(market: tuple[Bar, ...]) -> str:
        if not market:
            raise BacktestValidationError("backtest requires at least one bar")
        symbol = market[0].symbol
        previous: datetime | None = None
        for bar in market:
            if bar.symbol != symbol:
                raise BacktestValidationError("backtest accepts exactly one symbol")
            if previous is not None and bar.timestamp <= previous:
                raise BacktestValidationError("bars must be strictly chronological and unique")
            previous = bar.timestamp
        return symbol

    @staticmethod
    def _validate_history(
        history: tuple[Bar, ...], symbol: str, first_market_bar: Bar
    ) -> tuple[Bar, ...]:
        previous: datetime | None = None
        for bar in history:
            if bar.symbol != symbol:
                raise BacktestValidationError("history and backtest market must have one symbol")
            if previous is not None and bar.timestamp <= previous:
                raise BacktestValidationError(
                    "history bars must be strictly chronological and unique"
                )
            previous = bar.timestamp
        if history and history[-1].timestamp >= first_market_bar.timestamp:
            raise BacktestValidationError("history must end before the first backtest bar")
        return history
