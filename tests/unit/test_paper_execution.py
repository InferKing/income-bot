from datetime import UTC, datetime
from decimal import Decimal

import pytest

from income_tg.paper_trading import (
    ExecutionSettings,
    ExitReason,
    InstrumentKind,
    Liquidity,
    MarketSnapshot,
    OpenPositionResult,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperExecutionEngine,
    PositionSide,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def market(
    *,
    bid: str = "99",
    ask: str = "101",
    low: str = "98",
    high: str = "102",
) -> MarketSnapshot:
    return MarketSnapshot(
        observed_at=NOW,
        bid=Decimal(bid),
        ask=Decimal(ask),
        low=Decimal(low),
        high=Decimal(high),
    )


def order(
    *,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: str = "2",
    limit_price: str | None = None,
) -> Order:
    return Order(
        order_id="order-1",
        symbol="BTCUSDT",
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price) if limit_price else None,
    )


def open_long(
    engine: PaperExecutionEngine,
    *,
    leverage: int = 5,
    available_cash: str = "100000",
) -> OpenPositionResult:
    return engine.open_position(
        position_id="position-1",
        order=order(),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.LONG,
        leverage=leverage,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        available_cash=Decimal(available_cash),
    )


def test_market_buy_uses_ask_plus_slippage_and_taker_commission() -> None:
    engine = PaperExecutionEngine()

    result = engine.execute(order(), market())

    assert result.status is OrderStatus.FILLED
    assert result.fill is not None
    assert result.fill.price == Decimal("101.0202")
    assert result.fill.commission == Decimal("0.111122220")
    assert result.fill.liquidity is Liquidity.TAKER


def test_market_sell_uses_bid_minus_slippage() -> None:
    result = PaperExecutionEngine().execute(order(side=OrderSide.SELL), market())

    assert result.fill is not None
    assert result.fill.price == Decimal("98.9802")


@pytest.mark.parametrize(
    ("side", "limit_price"),
    [(OrderSide.BUY, "100"), (OrderSide.SELL, "100")],
)
def test_non_crossing_limit_order_stays_pending(
    side: OrderSide,
    limit_price: str,
) -> None:
    result = PaperExecutionEngine().execute(
        order(side=side, order_type=OrderType.LIMIT, limit_price=limit_price),
        market(),
    )

    assert result.status is OrderStatus.PENDING
    assert result.fill is None


@pytest.mark.parametrize(
    ("side", "limit_price", "expected_price"),
    [
        (OrderSide.BUY, "102", Decimal("101")),
        (OrderSide.SELL, "98", Decimal("99")),
    ],
)
def test_crossing_limit_receives_price_improvement_and_taker_fee(
    side: OrderSide,
    limit_price: str,
    expected_price: Decimal,
) -> None:
    result = PaperExecutionEngine().execute(
        order(side=side, order_type=OrderType.LIMIT, limit_price=limit_price),
        market(),
    )

    assert result.fill is not None
    assert result.fill.price == expected_price
    assert result.fill.commission == Decimal("2") * expected_price * Decimal("0.00055")
    assert result.fill.liquidity is Liquidity.TAKER


def test_open_long_reserves_margin_and_estimates_liquidation() -> None:
    result = open_long(PaperExecutionEngine())

    assert result.position is not None
    assert result.position.entry_price == Decimal("101.0202")
    assert result.position.margin == Decimal("40.40808")
    assert result.position.liquidation_price == Decimal("81.3212610")
    assert result.cash_delta == Decimal("-40.519202220")


def test_open_short_uses_sell_and_correct_liquidation_side() -> None:
    engine = PaperExecutionEngine()
    result = engine.open_position(
        position_id="short-1",
        order=order(side=OrderSide.SELL),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.SHORT,
        leverage=10,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        available_cash=Decimal("1000"),
    )

    assert result.position is not None
    assert result.position.liquidation_price == Decimal("108.3833190")


def test_insufficient_cash_rejects_open_without_cash_movement() -> None:
    result = open_long(PaperExecutionEngine(), available_cash="1")

    assert result.execution.status is OrderStatus.REJECTED
    assert result.execution.rejection_reason == "insufficient cash"
    assert result.position is None
    assert result.cash_delta == Decimal("0")


def test_pending_limit_does_not_create_position() -> None:
    engine = PaperExecutionEngine()
    result = engine.open_position(
        position_id="position-1",
        order=order(order_type=OrderType.LIMIT, limit_price="100"),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.LONG,
        leverage=5,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        available_cash=Decimal("1000"),
    )

    assert result.execution.status is OrderStatus.PENDING
    assert result.position is None
    assert result.cash_delta == Decimal("0")


@pytest.mark.parametrize(
    ("instrument", "side", "leverage"),
    [
        (InstrumentKind.SPOT, PositionSide.LONG, 1),
        (InstrumentKind.SPOT, PositionSide.SPOT, 2),
        (InstrumentKind.PERPETUAL, PositionSide.SPOT, 1),
    ],
)
def test_invalid_instrument_side_leverage_combinations_fail_fast(
    instrument: InstrumentKind,
    side: PositionSide,
    leverage: int,
) -> None:
    with pytest.raises(ValueError):
        PaperExecutionEngine().open_position(
            position_id="bad",
            order=order(),
            market=market(),
            instrument=instrument,
            side=side,
            leverage=leverage,
            stop_loss=Decimal("90"),
            take_profit=Decimal("120"),
            available_cash=Decimal("1000"),
        )


def test_spot_open_and_close_cash_flow_includes_both_commissions() -> None:
    engine = PaperExecutionEngine()
    opened = engine.open_position(
        position_id="spot-1",
        order=order(quantity="1"),
        market=market(),
        instrument=InstrumentKind.SPOT,
        side=PositionSide.SPOT,
        leverage=1,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        available_cash=Decimal("1000"),
    )
    assert opened.position is not None
    assert opened.position.liquidation_price is None
    assert opened.cash_delta == Decimal("-101.075761110")

    closed = engine.close_position(
        opened.position,
        market(bid="110", ask="111", low="109", high="112"),
    )

    assert closed.fill.quantity == Decimal("1")
    assert closed.fill.price == Decimal("109.9780")
    assert closed.gross_pnl == Decimal("8.9578")
    assert closed.net_pnl == Decimal("8.841750990")
    assert closed.cash_delta == Decimal("109.91751210")


def test_long_pays_positive_funding_and_short_receives_it() -> None:
    engine = PaperExecutionEngine()
    long_position = open_long(engine).position
    assert long_position is not None
    short_opened = engine.open_position(
        position_id="short-1",
        order=order(side=OrderSide.SELL),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.SHORT,
        leverage=5,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        available_cash=Decimal("1000"),
    )
    assert short_opened.position is not None

    long_result = engine.apply_funding(
        long_position,
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )
    short_result = engine.apply_funding(
        short_opened.position,
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )

    assert long_result.funding_pnl == Decimal("-0.200")
    assert long_result.position.funding_pnl == Decimal("-0.200")
    assert long_result.cash_delta == Decimal("0")
    assert short_result.funding_pnl == Decimal("0.200")


def test_funding_is_accrued_and_posted_exactly_once_at_close() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    opened = open_long(engine, leverage=5)
    assert opened.position is not None

    first = engine.apply_funding(
        opened.position,
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )
    second = engine.apply_funding(
        first.position,
        funding_rate=Decimal("0.002"),
        mark_price=Decimal("100"),
    )
    closed = engine.close_position(
        second.position,
        market(bid="101.0202", ask="101.0202", low="101.0202", high="101.0202"),
    )

    assert first.cash_delta == second.cash_delta == Decimal("0")
    assert second.position.funding_pnl == Decimal("-0.600")
    assert closed.cash_delta == (
        opened.position.margin
        + closed.gross_pnl
        + second.position.funding_pnl
        - closed.fill.commission
    )
    assert opened.cash_delta + first.cash_delta + second.cash_delta + closed.cash_delta == (
        closed.net_pnl
    )


def test_funding_does_not_apply_to_spot() -> None:
    engine = PaperExecutionEngine()
    opened = engine.open_position(
        position_id="spot-1",
        order=order(quantity="1"),
        market=market(),
        instrument=InstrumentKind.SPOT,
        side=PositionSide.SPOT,
        leverage=1,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        available_cash=Decimal("1000"),
    )
    assert opened.position is not None

    result = engine.apply_funding(
        opened.position,
        funding_rate=Decimal("0.01"),
        mark_price=Decimal("100"),
    )

    assert result.funding_pnl == Decimal("0")
    assert result.position == opened.position


def test_close_short_calculates_directional_pnl_and_returns_margin() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    opened = engine.open_position(
        position_id="short-1",
        order=order(side=OrderSide.SELL, quantity="1"),
        market=market(bid="100", ask="100", low="100", high="100"),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.SHORT,
        leverage=5,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        available_cash=Decimal("1000"),
    )
    assert opened.position is not None

    closed = engine.close_position(
        opened.position,
        market(bid="89", ask="90", low="89", high="90"),
    )

    assert closed.gross_pnl == Decimal("10")
    assert closed.net_pnl == Decimal("9.89550")
    assert closed.cash_delta == Decimal("29.95050")
    assert closed.fill.quantity == opened.position.quantity


def test_stop_wins_when_stop_and_take_profit_are_both_in_same_bar() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    position = open_long(engine, leverage=1).position
    assert position is not None

    result = engine.evaluate_exit(
        position,
        market(bid="100", ask="100", low="89", high="121"),
    )

    assert result is not None
    assert result.reason is ExitReason.STOP_LOSS
    assert result.fill.price == Decimal("90")


def test_take_profit_fully_closes_short() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    opened = engine.open_position(
        position_id="short-1",
        order=order(side=OrderSide.SELL),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.SHORT,
        leverage=2,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        available_cash=Decimal("1000"),
    )
    assert opened.position is not None

    result = engine.evaluate_exit(
        opened.position,
        market(bid="91", ask="92", low="89", high="95"),
    )

    assert result is not None
    assert result.reason is ExitReason.TAKE_PROFIT
    assert result.fill.quantity == opened.position.quantity
    assert result.fill.price == Decimal("90")


def test_liquidation_has_conservative_priority_on_gap_bar() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    position = open_long(engine, leverage=5).position
    assert position is not None
    assert position.liquidation_price is not None

    result = engine.evaluate_exit(
        position,
        market(bid="80", ask="81", low="70", high="100"),
    )

    assert result is not None
    assert result.reason is ExitReason.LIQUIDATION
    assert result.fill.price == Decimal("80")


def test_long_stop_uses_adverse_bid_after_gap_through_trigger() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    position = open_long(engine, leverage=1).position
    assert position is not None

    result = engine.evaluate_exit(
        position,
        market(bid="75", ask="76", low="70", high="89"),
    )

    assert result is not None
    assert result.reason is ExitReason.STOP_LOSS
    assert result.fill.price == Decimal("75")


def test_short_stop_uses_adverse_ask_after_gap_through_trigger() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    opened = engine.open_position(
        position_id="short-1",
        order=order(side=OrderSide.SELL),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.SHORT,
        leverage=1,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        available_cash=Decimal("1000"),
    )
    assert opened.position is not None

    result = engine.evaluate_exit(
        opened.position,
        market(bid="124", ask="125", low="111", high="130"),
    )

    assert result is not None
    assert result.reason is ExitReason.STOP_LOSS
    assert result.fill.price == Decimal("125")


def test_take_profit_uses_better_executable_quote_after_favorable_gap() -> None:
    engine = PaperExecutionEngine(ExecutionSettings(slippage_bps=Decimal("0")))
    position = open_long(engine, leverage=1).position
    assert position is not None

    result = engine.evaluate_exit(
        position,
        market(bid="125", ask="126", low="121", high="130"),
    )

    assert result is not None
    assert result.reason is ExitReason.TAKE_PROFIT
    assert result.fill.price == Decimal("125")


def test_no_exit_when_bar_touches_neither_level() -> None:
    position = open_long(PaperExecutionEngine()).position
    assert position is not None

    assert (
        PaperExecutionEngine().evaluate_exit(
            position,
            market(bid="100", ask="101", low="95", high="110"),
        )
        is None
    )


def test_wrong_open_order_side_is_rejected() -> None:
    result = PaperExecutionEngine().open_position(
        position_id="position-1",
        order=order(side=OrderSide.SELL),
        market=market(),
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.LONG,
        leverage=2,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        available_cash=Decimal("1000"),
    )

    assert result.execution.status is OrderStatus.REJECTED
    assert result.position is None


@pytest.mark.parametrize("leverage", [1, 5, 20])
def test_liquidation_estimate_moves_toward_entry_with_higher_leverage(
    leverage: int,
) -> None:
    engine = PaperExecutionEngine()
    long_price = engine.liquidation_estimate(
        entry_price=Decimal("100"),
        side=PositionSide.LONG,
        leverage=leverage,
    )
    short_price = engine.liquidation_estimate(
        entry_price=Decimal("100"),
        side=PositionSide.SHORT,
        leverage=leverage,
    )

    assert long_price is not None and long_price < Decimal("100")
    assert short_price is not None and short_price > Decimal("100")
