from __future__ import annotations

from decimal import Decimal

from income_tg.paper_trading.models import (
    CloseResult,
    ExecutionSettings,
    ExitReason,
    Fill,
    FundingResult,
    InstrumentKind,
    Liquidity,
    MarketSnapshot,
    OpenPositionResult,
    Order,
    OrderExecution,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperPosition,
    PositionSide,
)

ZERO = Decimal("0")
BPS = Decimal("10000")


class PaperExecutionEngine:
    """Stateless deterministic execution model for live paper and backtests."""

    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        self.settings = settings or ExecutionSettings()

    def execute(self, order: Order, market: MarketSnapshot) -> OrderExecution:
        if order.order_type is OrderType.LIMIT:
            return self._execute_limit(order, market)
        reference = market.ask if order.side is OrderSide.BUY else market.bid
        price = self._with_slippage(reference, order.side)
        return self._filled(order, price, market, Liquidity.TAKER)

    def open_position(
        self,
        *,
        position_id: str,
        order: Order,
        market: MarketSnapshot,
        instrument: InstrumentKind,
        side: PositionSide,
        leverage: int,
        stop_loss: Decimal,
        take_profit: Decimal,
        available_cash: Decimal,
    ) -> OpenPositionResult:
        self._validate_position_kind(instrument, side, leverage)
        if not available_cash.is_finite() or available_cash < ZERO:
            raise ValueError("available_cash must be finite and nonnegative")
        execution = self.execute(order, market)
        if execution.status is not OrderStatus.FILLED:
            return OpenPositionResult(execution=execution, position=None, cash_delta=ZERO)
        fill = execution.fill
        assert fill is not None
        expected_side = OrderSide.SELL if side is PositionSide.SHORT else OrderSide.BUY
        if fill.side is not expected_side:
            return self._rejected_open(order, "order side does not open requested position")
        self._validate_exit_levels(side, fill.price, stop_loss, take_profit)

        notional = fill.notional
        margin = notional if instrument is InstrumentKind.SPOT else notional / Decimal(leverage)
        required_cash = margin + fill.commission
        if required_cash > available_cash:
            return self._rejected_open(order, "insufficient cash")

        liquidation_price = self.liquidation_estimate(
            entry_price=fill.price,
            side=side,
            leverage=leverage,
        )
        position = PaperPosition(
            position_id=position_id,
            symbol=order.symbol,
            instrument=instrument,
            side=side,
            quantity=fill.quantity,
            entry_price=fill.price,
            leverage=leverage,
            margin=margin,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opening_commission=fill.commission,
            funding_pnl=ZERO,
            opened_at=fill.filled_at,
            liquidation_price=liquidation_price,
        )
        return OpenPositionResult(
            execution=execution,
            position=position,
            cash_delta=-required_cash,
        )

    def apply_funding(
        self,
        position: PaperPosition,
        *,
        funding_rate: Decimal,
        mark_price: Decimal,
    ) -> FundingResult:
        if not funding_rate.is_finite():
            raise ValueError("funding_rate must be finite")
        if not mark_price.is_finite() or mark_price <= ZERO:
            raise ValueError("mark_price must be finite and positive")
        if position.instrument is InstrumentKind.SPOT:
            funding_pnl = ZERO
        else:
            payer_sign = Decimal("-1") if position.side is PositionSide.LONG else Decimal("1")
            funding_pnl = position.quantity * mark_price * funding_rate * payer_sign
        return FundingResult(
            position=position.with_funding(funding_pnl),
            funding_pnl=funding_pnl,
        )

    def close_position(
        self,
        position: PaperPosition,
        market: MarketSnapshot,
        *,
        reason: ExitReason = ExitReason.MANUAL,
    ) -> CloseResult:
        side = self._closing_side(position)
        order = Order(
            order_id=f"close:{position.position_id}",
            symbol=position.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=position.quantity,
        )
        execution = self.execute(order, market)
        fill = execution.fill
        assert fill is not None
        return self._closed(position, fill, reason)

    def evaluate_exit(
        self,
        position: PaperPosition,
        market: MarketSnapshot,
    ) -> CloseResult | None:
        trigger = self._trigger(position, market)
        if trigger is None:
            return None
        reason, trigger_price = trigger
        side = self._closing_side(position)
        reference_price = self._gap_aware_exit_price(
            trigger_price=trigger_price,
            side=side,
            reason=reason,
            market=market,
        )
        price = self._with_slippage(reference_price, side)
        order = Order(
            order_id=f"{reason.value.lower()}:{position.position_id}",
            symbol=position.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=position.quantity,
        )
        execution = self._filled(order, price, market, Liquidity.TAKER)
        fill = execution.fill
        assert fill is not None
        return self._closed(position, fill, reason)

    def liquidation_estimate(
        self,
        *,
        entry_price: Decimal,
        side: PositionSide,
        leverage: int,
    ) -> Decimal | None:
        if not entry_price.is_finite() or entry_price <= ZERO:
            raise ValueError("entry_price must be finite and positive")
        if not 1 <= leverage <= 20:
            raise ValueError("leverage must be in [1, 20]")
        if side is PositionSide.SPOT:
            if leverage != 1:
                raise ValueError("spot leverage must be 1")
            return None
        inverse_leverage = Decimal("1") / Decimal(leverage)
        if side is PositionSide.LONG:
            return max(
                ZERO,
                entry_price
                * (Decimal("1") - inverse_leverage + self.settings.maintenance_margin_rate),
            )
        return entry_price * (
            Decimal("1") + inverse_leverage - self.settings.maintenance_margin_rate
        )

    def _execute_limit(self, order: Order, market: MarketSnapshot) -> OrderExecution:
        assert order.limit_price is not None
        crosses = (
            market.ask <= order.limit_price
            if order.side is OrderSide.BUY
            else market.bid >= order.limit_price
        )
        if not crosses:
            return OrderExecution(status=OrderStatus.PENDING)
        price = (
            min(market.ask, order.limit_price)
            if order.side is OrderSide.BUY
            else max(market.bid, order.limit_price)
        )
        return self._filled(order, price, market, Liquidity.TAKER)

    @staticmethod
    def _gap_aware_exit_price(
        *,
        trigger_price: Decimal,
        side: OrderSide,
        reason: ExitReason,
        market: MarketSnapshot,
    ) -> Decimal:
        """Use an already-crossed executable quote instead of an unreachable trigger.

        Stops and liquidations are market exits, so a gap through the trigger receives
        the adverse bid/ask. Take-profit exits receive price improvement when the
        executable quote has already moved beyond the target.
        """

        quote = market.ask if side is OrderSide.BUY else market.bid
        adverse_exit = reason in (ExitReason.STOP_LOSS, ExitReason.LIQUIDATION)
        if side is OrderSide.SELL:
            return min(trigger_price, quote) if adverse_exit else max(trigger_price, quote)
        return max(trigger_price, quote) if adverse_exit else min(trigger_price, quote)

    def _filled(
        self,
        order: Order,
        price: Decimal,
        market: MarketSnapshot,
        liquidity: Liquidity,
    ) -> OrderExecution:
        fee_rate = (
            self.settings.maker_fee_rate
            if liquidity is Liquidity.MAKER
            else self.settings.taker_fee_rate
        )
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            commission=order.quantity * price * fee_rate,
            liquidity=liquidity,
            filled_at=market.observed_at,
        )
        return OrderExecution(status=OrderStatus.FILLED, fill=fill)

    def _with_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        slippage = self.settings.slippage_bps / BPS
        multiplier = Decimal("1") + slippage if side is OrderSide.BUY else Decimal("1") - slippage
        return price * multiplier

    def _closed(
        self,
        position: PaperPosition,
        fill: Fill,
        reason: ExitReason,
    ) -> CloseResult:
        direction = Decimal("-1") if position.side is PositionSide.SHORT else Decimal("1")
        gross_pnl = (fill.price - position.entry_price) * position.quantity * direction
        net_pnl = gross_pnl + position.funding_pnl - position.opening_commission - fill.commission
        if position.instrument is InstrumentKind.SPOT:
            cash_delta = fill.notional - fill.commission
        else:
            cash_delta = position.margin + gross_pnl + position.funding_pnl - fill.commission
        return CloseResult(
            position=position,
            fill=fill,
            reason=reason,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            cash_delta=cash_delta,
        )

    def _trigger(
        self,
        position: PaperPosition,
        market: MarketSnapshot,
    ) -> tuple[ExitReason, Decimal] | None:
        if position.side in (PositionSide.SPOT, PositionSide.LONG):
            if position.liquidation_price is not None and market.low <= position.liquidation_price:
                return ExitReason.LIQUIDATION, position.liquidation_price
            if market.low <= position.stop_loss:
                return ExitReason.STOP_LOSS, position.stop_loss
            if market.high >= position.take_profit:
                return ExitReason.TAKE_PROFIT, position.take_profit
        else:
            if position.liquidation_price is not None and market.high >= position.liquidation_price:
                return ExitReason.LIQUIDATION, position.liquidation_price
            if market.high >= position.stop_loss:
                return ExitReason.STOP_LOSS, position.stop_loss
            if market.low <= position.take_profit:
                return ExitReason.TAKE_PROFIT, position.take_profit
        return None

    @staticmethod
    def _validate_position_kind(
        instrument: InstrumentKind,
        side: PositionSide,
        leverage: int,
    ) -> None:
        if not 1 <= leverage <= 20:
            raise ValueError("leverage must be in [1, 20]")
        if instrument is InstrumentKind.SPOT and (side is not PositionSide.SPOT or leverage != 1):
            raise ValueError("spot instrument requires SPOT side and 1x leverage")
        if instrument is InstrumentKind.PERPETUAL and side is PositionSide.SPOT:
            raise ValueError("perpetual instrument requires LONG or SHORT side")

    @staticmethod
    def _validate_exit_levels(
        side: PositionSide,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
    ) -> None:
        if not stop_loss.is_finite() or not take_profit.is_finite():
            raise ValueError("exit prices must be finite")
        if side in (PositionSide.SPOT, PositionSide.LONG):
            if not stop_loss < entry_price < take_profit:
                raise ValueError("long/spot exits must satisfy stop < entry < take profit")
        elif not take_profit < entry_price < stop_loss:
            raise ValueError("short exits must satisfy take profit < entry < stop")

    @staticmethod
    def _closing_side(position: PaperPosition) -> OrderSide:
        return OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL

    @staticmethod
    def _rejected_open(order: Order, reason: str) -> OpenPositionResult:
        return OpenPositionResult(
            execution=OrderExecution(
                status=OrderStatus.REJECTED,
                rejection_reason=reason,
            ),
            position=None,
            cash_delta=ZERO,
        )
