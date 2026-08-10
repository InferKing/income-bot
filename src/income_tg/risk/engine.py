from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from income_tg.risk.models import (
    MarketGuard,
    PositionDirection,
    RejectionReason,
    RiskDecision,
    RiskLimits,
    SizingRequest,
    SizingResult,
)

ZERO = Decimal("0")


class RiskEngine:
    """Pure, deterministic pre-trade risk gate and position sizer."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def assess(self, request: SizingRequest, *, now: datetime) -> RiskDecision:
        reasons = list(self._guard_reasons(request, now=now))
        if reasons:
            return RiskDecision(sizing=None, reasons=tuple(reasons))

        portfolio = request.portfolio
        effective_entry = self._effective_entry(request)
        stop_distance = abs(effective_entry - request.stop_price)
        stop_distance_fraction = stop_distance / effective_entry
        risk_fraction = min(
            request.requested_risk_fraction or self.limits.max_stop_risk_fraction,
            self.limits.max_stop_risk_fraction,
        )
        risk_budget = portfolio.equity * risk_fraction
        risk_per_unit, cost_buffer_per_unit = self._risk_per_unit(
            request,
            effective_entry=effective_entry,
            stop_distance=stop_distance,
        )
        risk_limited_quantity = risk_budget / risk_per_unit

        margin_limit = portfolio.equity * self.limits.max_margin_fraction
        if margin_limit <= ZERO or portfolio.available_cash <= ZERO:
            return RiskDecision(sizing=None, reasons=(RejectionReason.INSUFFICIENT_CASH,))

        entry_fee_rate = request.execution_costs.taker_fee_rate
        if request.direction is PositionDirection.SPOT:
            maximum_notional = min(
                margin_limit,
                portfolio.available_cash / (Decimal("1") + entry_fee_rate),
            )
        else:
            maximum_leverage = Decimal(self.limits.max_leverage)
            maximum_notional = min(
                margin_limit * maximum_leverage,
                portfolio.available_cash / (Decimal("1") / maximum_leverage + entry_fee_rate),
            )
        raw_quantity = min(risk_limited_quantity, maximum_notional / effective_entry)
        quantity = self._round_quantity_down(raw_quantity, request.venue.quantity_step)

        minimum_quantity = request.venue.minimum_quantity
        if quantity <= ZERO or (minimum_quantity is not None and quantity < minimum_quantity):
            return RiskDecision(sizing=None, reasons=(RejectionReason.BELOW_MIN_QUANTITY,))

        notional = quantity * effective_entry
        minimum_notional = max(
            self.limits.min_notional,
            request.venue.minimum_notional or ZERO,
        )
        if notional < minimum_notional:
            return RiskDecision(sizing=None, reasons=(RejectionReason.BELOW_MIN_NOTIONAL,))

        entry_fee = notional * entry_fee_rate
        if request.direction is PositionDirection.SPOT:
            leverage = 1
        else:
            cash_for_margin = portfolio.available_cash - entry_fee
            if cash_for_margin <= ZERO:
                return RiskDecision(sizing=None, reasons=(RejectionReason.INSUFFICIENT_CASH,))
            required_for_margin = (notional / margin_limit).to_integral_value(
                rounding=ROUND_CEILING
            )
            required_for_cash = (notional / cash_for_margin).to_integral_value(
                rounding=ROUND_CEILING
            )
            leverage = max(1, int(required_for_margin), int(required_for_cash))
            if leverage > self.limits.max_leverage:
                return RiskDecision(sizing=None, reasons=(RejectionReason.INSUFFICIENT_CASH,))

        margin = notional / Decimal(leverage)
        price_loss_amount = quantity * stop_distance
        cost_buffer_amount = quantity * cost_buffer_per_unit
        stop_loss_amount = price_loss_amount + cost_buffer_amount
        if stop_loss_amount > risk_budget or margin > margin_limit:
            raise AssertionError("rounded risk sizing violated configured limits")
        if margin + entry_fee > portfolio.available_cash:
            raise AssertionError("rounded risk sizing violated available cash")
        return RiskDecision(
            sizing=SizingResult(
                quantity=quantity,
                notional=notional,
                margin=margin,
                leverage=leverage,
                stop_loss_amount=stop_loss_amount,
                stop_distance_fraction=stop_distance_fraction,
                effective_entry_price=effective_entry,
                price_loss_amount=price_loss_amount,
                cost_buffer_amount=cost_buffer_amount,
            )
        )

    @staticmethod
    def _effective_entry(request: SizingRequest) -> Decimal:
        if request.direction in (PositionDirection.SPOT, PositionDirection.LONG):
            return max(request.entry_price, request.market.ask)
        return min(request.entry_price, request.market.bid)

    @staticmethod
    def _risk_per_unit(
        request: SizingRequest,
        *,
        effective_entry: Decimal,
        stop_distance: Decimal,
    ) -> tuple[Decimal, Decimal]:
        costs = request.execution_costs
        round_trip_fee = (effective_entry + request.stop_price) * costs.taker_fee_rate
        spread_buffer = request.market.ask - request.market.bid
        slippage_rate = costs.slippage_bps / Decimal("10000")
        slippage_buffer = (effective_entry + request.stop_price) * slippage_rate
        funding_buffer = effective_entry * costs.funding_buffer_rate
        cost_buffer = round_trip_fee + spread_buffer + slippage_buffer + funding_buffer
        return stop_distance + cost_buffer, cost_buffer

    @staticmethod
    def _round_quantity_down(quantity: Decimal, step: Decimal | None) -> Decimal:
        if step is None:
            return quantity
        steps = (quantity / step).to_integral_value(rounding=ROUND_FLOOR)
        return steps * step

    def _guard_reasons(
        self,
        request: SizingRequest,
        *,
        now: datetime,
    ) -> tuple[RejectionReason, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        reasons: list[RejectionReason] = []
        market: MarketGuard = request.market
        age = now - market.observed_at
        if age > self.limits.max_market_age:
            reasons.append(RejectionReason.STALE_MARKET_DATA)
        if age < -self.limits.max_future_skew:
            reasons.append(RejectionReason.FUTURE_MARKET_DATA)
        if market.spread_bps > self.limits.max_spread_bps:
            reasons.append(RejectionReason.SPREAD_TOO_WIDE)

        portfolio = request.portfolio
        if portfolio.daily_loss_fraction >= self.limits.max_daily_loss_fraction:
            reasons.append(RejectionReason.DAILY_LOSS_LIMIT)
        if portfolio.drawdown_fraction >= self.limits.max_drawdown_fraction:
            reasons.append(RejectionReason.DRAWDOWN_LIMIT)
        if portfolio.open_position_count >= self.limits.max_open_positions:
            reasons.append(RejectionReason.OPEN_POSITION_LIMIT)

        effective_entry = self._effective_entry(request)
        valid_stop = (
            request.stop_price < effective_entry
            if request.direction in (PositionDirection.SPOT, PositionDirection.LONG)
            else request.stop_price > effective_entry
        )
        if not valid_stop:
            reasons.append(RejectionReason.INVALID_STOP)
        return tuple(reasons)
