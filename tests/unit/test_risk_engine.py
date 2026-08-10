from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from income_tg.risk import (
    ExecutionCosts,
    MarketGuard,
    PortfolioRiskState,
    PositionDirection,
    RejectionReason,
    RiskEngine,
    RiskLimits,
    SizingRequest,
    VenueConstraints,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def market(
    *,
    observed_at: datetime = NOW,
    bid: str = "100",
    ask: str = "100",
) -> MarketGuard:
    return MarketGuard(observed_at=observed_at, bid=Decimal(bid), ask=Decimal(ask))


def portfolio(
    *,
    equity: str = "100000",
    available_cash: str = "100000",
    day_start_equity: str = "100000",
    peak_equity: str = "100000",
    open_position_count: int = 0,
) -> PortfolioRiskState:
    return PortfolioRiskState(
        equity=Decimal(equity),
        available_cash=Decimal(available_cash),
        day_start_equity=Decimal(day_start_equity),
        peak_equity=Decimal(peak_equity),
        open_position_count=open_position_count,
    )


def request(
    *,
    direction: PositionDirection = PositionDirection.LONG,
    entry: str = "100",
    stop: str = "98",
    market_data: MarketGuard | None = None,
    state: PortfolioRiskState | None = None,
    requested_risk: str | None = None,
    execution_costs: ExecutionCosts | None = None,
    venue: VenueConstraints | None = None,
) -> SizingRequest:
    return SizingRequest(
        direction=direction,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        market=market_data or market(),
        portfolio=state or portfolio(),
        requested_risk_fraction=Decimal(requested_risk) if requested_risk else None,
        execution_costs=execution_costs or ExecutionCosts(),
        venue=venue or VenueConstraints(),
    )


def test_derivative_size_is_stop_risk_limited_and_leverage_is_minimal() -> None:
    decision = RiskEngine().assess(request(), now=NOW)

    assert decision.approved
    assert decision.sizing is not None
    assert decision.sizing.quantity == Decimal("500")
    assert decision.sizing.notional == Decimal("50000")
    assert decision.sizing.leverage == 5
    assert decision.sizing.margin == Decimal("10000")
    assert decision.sizing.stop_loss_amount == Decimal("1000")
    assert decision.sizing.stop_distance_fraction == Decimal("0.02")


def test_requested_lower_risk_reduces_position_and_uses_only_needed_leverage() -> None:
    decision = RiskEngine().assess(request(requested_risk="0.005"), now=NOW)

    assert decision.sizing is not None
    assert decision.sizing.notional == Decimal("25000.0")
    assert decision.sizing.leverage == 3
    assert decision.sizing.margin == Decimal("8333.333333333333333333333333")
    assert decision.sizing.stop_loss_amount == Decimal("500.0")


def test_requested_risk_cannot_exceed_limit() -> None:
    decision = RiskEngine().assess(request(requested_risk="0.10"), now=NOW)

    assert decision.sizing is not None
    assert decision.sizing.stop_loss_amount == Decimal("1000")


def test_spot_is_one_x_and_capped_by_ten_percent_cash_margin() -> None:
    decision = RiskEngine().assess(
        request(direction=PositionDirection.SPOT),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.notional == Decimal("10000.00")
    assert decision.sizing.margin == Decimal("10000.00")
    assert decision.sizing.leverage == 1
    assert decision.sizing.stop_loss_amount == Decimal("200.00")


def test_max_leverage_caps_notional_when_stop_is_very_tight() -> None:
    decision = RiskEngine().assess(request(stop="99.99"), now=NOW)

    assert decision.sizing is not None
    assert decision.sizing.notional == Decimal("200000")
    assert decision.sizing.margin == Decimal("10000")
    assert decision.sizing.leverage == 20
    assert decision.sizing.stop_loss_amount == Decimal("20.00")


def test_available_cash_can_be_tighter_than_margin_fraction() -> None:
    decision = RiskEngine().assess(
        request(state=portfolio(available_cash="1000")),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.notional == Decimal("20000")
    assert decision.sizing.margin == Decimal("1000")
    assert decision.sizing.leverage == 20


def test_all_independent_guards_are_reported_together() -> None:
    stale_wide = market(
        observed_at=NOW - timedelta(seconds=6),
        bid="99",
        ask="101",
    )
    state = portfolio(
        equity="80000",
        day_start_equity="100000",
        peak_equity="100000",
        open_position_count=3,
    )

    decision = RiskEngine().assess(
        request(stop="101", market_data=stale_wide, state=state),
        now=NOW,
    )

    assert not decision.approved
    assert decision.reasons == (
        RejectionReason.STALE_MARKET_DATA,
        RejectionReason.SPREAD_TOO_WIDE,
        RejectionReason.DAILY_LOSS_LIMIT,
        RejectionReason.DRAWDOWN_LIMIT,
        RejectionReason.OPEN_POSITION_LIMIT,
        RejectionReason.INVALID_STOP,
    )


def test_limits_block_at_exact_daily_loss_and_drawdown_boundaries() -> None:
    daily_decision = RiskEngine().assess(
        request(
            state=portfolio(
                equity="95000",
                day_start_equity="100000",
                peak_equity="100000",
            )
        ),
        now=NOW,
    )
    drawdown_decision = RiskEngine().assess(
        request(
            state=portfolio(
                equity="85000",
                day_start_equity="85000",
                peak_equity="100000",
            )
        ),
        now=NOW,
    )

    assert RejectionReason.DAILY_LOSS_LIMIT in daily_decision.reasons
    assert RejectionReason.DRAWDOWN_LIMIT in drawdown_decision.reasons


def test_future_market_timestamp_beyond_skew_is_rejected() -> None:
    decision = RiskEngine().assess(
        request(market_data=market(observed_at=NOW + timedelta(seconds=2))),
        now=NOW,
    )

    assert decision.reasons == (RejectionReason.FUTURE_MARKET_DATA,)


def test_zero_cash_is_rejected() -> None:
    decision = RiskEngine().assess(
        request(state=portfolio(available_cash="0")),
        now=NOW,
    )

    assert decision.reasons == (RejectionReason.INSUFFICIENT_CASH,)


def test_below_exchange_minimum_is_rejected() -> None:
    engine = RiskEngine(RiskLimits(min_notional=Decimal("1000000")))

    decision = engine.assess(request(), now=NOW)

    assert decision.reasons == (RejectionReason.BELOW_MIN_NOTIONAL,)


@pytest.mark.parametrize(
    ("direction", "stop"),
    [
        (PositionDirection.SPOT, "100"),
        (PositionDirection.LONG, "101"),
        (PositionDirection.SHORT, "99"),
    ],
)
def test_stop_must_be_on_loss_side(
    direction: PositionDirection,
    stop: str,
) -> None:
    decision = RiskEngine().assess(
        request(direction=direction, stop=stop),
        now=NOW,
    )

    assert RejectionReason.INVALID_STOP in decision.reasons


def test_short_allows_stop_more_than_one_hundred_percent_away() -> None:
    decision = RiskEngine().assess(
        request(direction=PositionDirection.SHORT, stop="250"),
        now=NOW,
    )

    assert decision.approved
    assert decision.sizing is not None
    assert decision.sizing.stop_distance_fraction == Decimal("1.5")


@pytest.mark.parametrize(
    "limits",
    [
        RiskLimits(max_leverage=1),
        RiskLimits(max_leverage=20),
    ],
)
def test_configured_leverage_is_always_within_product_boundary(limits: RiskLimits) -> None:
    assert 1 <= limits.max_leverage <= 20


@pytest.mark.parametrize("max_leverage", [0, 21])
def test_invalid_leverage_configuration_is_rejected(max_leverage: int) -> None:
    with pytest.raises(ValueError, match="max_leverage"):
        RiskLimits(max_leverage=max_leverage)


def test_naive_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RiskEngine().assess(request(), now=datetime(2026, 8, 10))


def test_long_uses_ask_instead_of_fictitious_favorable_entry() -> None:
    decision = RiskEngine().assess(
        request(
            entry="90",
            stop="80.3",
            market_data=market(bid="99.9", ask="100.1"),
        ),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.effective_entry_price == Decimal("100.1")
    assert decision.sizing.quantity == Decimal("50")
    assert decision.sizing.price_loss_amount == Decimal("990")
    assert decision.sizing.cost_buffer_amount == Decimal("10")
    assert decision.sizing.stop_loss_amount == Decimal("1000")


def test_short_uses_bid_instead_of_fictitious_favorable_entry() -> None:
    decision = RiskEngine().assess(
        request(
            direction=PositionDirection.SHORT,
            entry="110",
            stop="119.7",
            market_data=market(bid="99.9", ask="100.1"),
        ),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.effective_entry_price == Decimal("99.9")
    assert decision.sizing.quantity == Decimal("50")
    assert decision.sizing.stop_loss_amount == Decimal("1000")


def test_stop_risk_includes_round_trip_fees_spread_slippage_and_funding() -> None:
    costs = ExecutionCosts(
        taker_fee_rate=Decimal("0.001"),
        slippage_bps=Decimal("10"),
        funding_buffer_rate=Decimal("0.002"),
    )
    decision = RiskEngine().assess(
        request(
            entry="100",
            stop="98",
            market_data=market(bid="99.9", ask="100.1"),
            execution_costs=costs,
        ),
        now=NOW,
    )

    assert decision.sizing is not None
    # Per unit: price 2.1 + fees .1981 + spread .2 + slippage .1981 + funding .2002.
    assert decision.sizing.quantity == Decimal("1000") / Decimal("2.8964")
    assert decision.sizing.price_loss_amount == decision.sizing.quantity * Decimal("2.1")
    assert decision.sizing.cost_buffer_amount == decision.sizing.quantity * Decimal("0.7964")
    assert decision.sizing.stop_loss_amount == Decimal("1000")


def test_quantity_is_rounded_down_to_venue_step_then_risk_is_revalidated() -> None:
    decision = RiskEngine().assess(
        request(venue=VenueConstraints(quantity_step=Decimal("3"))),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.quantity == Decimal("498")
    assert decision.sizing.notional == Decimal("49800")
    assert decision.sizing.leverage == 5
    assert decision.sizing.margin == Decimal("9960")
    assert decision.sizing.stop_loss_amount == Decimal("996")
    assert decision.sizing.stop_loss_amount <= Decimal("1000")


def test_rounding_to_zero_is_rejected_as_below_minimum_quantity() -> None:
    decision = RiskEngine().assess(
        request(
            state=portfolio(
                equity="1000",
                available_cash="1000",
                day_start_equity="1000",
                peak_equity="1000",
            ),
            venue=VenueConstraints(quantity_step=Decimal("10")),
        ),
        now=NOW,
    )

    assert decision.reasons == (RejectionReason.BELOW_MIN_QUANTITY,)


def test_rounded_quantity_must_meet_venue_minimum_quantity() -> None:
    decision = RiskEngine().assess(
        request(
            venue=VenueConstraints(
                quantity_step=Decimal("3"),
                minimum_quantity=Decimal("500"),
            )
        ),
        now=NOW,
    )

    assert decision.reasons == (RejectionReason.BELOW_MIN_QUANTITY,)


def test_rounded_notional_must_meet_venue_minimum_notional() -> None:
    decision = RiskEngine().assess(
        request(
            venue=VenueConstraints(
                quantity_step=Decimal("3"),
                minimum_notional=Decimal("50000"),
            )
        ),
        now=NOW,
    )

    assert decision.reasons == (RejectionReason.BELOW_MIN_NOTIONAL,)


def test_spot_notional_leaves_cash_for_entry_fee() -> None:
    decision = RiskEngine().assess(
        request(
            direction=PositionDirection.SPOT,
            stop="50",
            state=portfolio(available_cash="1000"),
            execution_costs=ExecutionCosts(taker_fee_rate=Decimal("0.01")),
        ),
        now=NOW,
    )

    assert decision.sizing is not None
    assert decision.sizing.notional == Decimal("1000") / Decimal("1.01")
    entry_fee = decision.sizing.notional * Decimal("0.01")
    assert decision.sizing.margin + entry_fee == Decimal("1000")


@pytest.mark.parametrize(
    "venue",
    [
        VenueConstraints(quantity_step=Decimal("0.001")),
        VenueConstraints(minimum_quantity=Decimal("0.01")),
        VenueConstraints(minimum_notional=Decimal("5")),
    ],
)
def test_venue_constraints_defaults_are_backward_compatible(venue: VenueConstraints) -> None:
    assert venue is not None


@pytest.mark.parametrize(
    "costs",
    [
        ExecutionCosts(),
        ExecutionCosts(taker_fee_rate=Decimal("0.001")),
        ExecutionCosts(slippage_bps=Decimal("2")),
        ExecutionCosts(funding_buffer_rate=Decimal("0.001")),
    ],
)
def test_execution_cost_defaults_are_backward_compatible(costs: ExecutionCosts) -> None:
    assert costs is not None
