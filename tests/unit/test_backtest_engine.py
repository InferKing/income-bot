from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from income_tg.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestValidationError,
    Bar,
    BuyAndHoldStrategy,
    StrategyContext,
    TargetSignal,
)


def bars(prices: list[str]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            timestamp=start + timedelta(hours=index),
            symbol="BTCUSDT",
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal("1"),
        )
        for index, price in enumerate(prices)
    ]


def test_signal_is_filled_only_at_next_bar_open() -> None:
    result = BacktestEngine(BacktestConfig(fee_rate=Decimal("0"), slippage_bps=Decimal("0"))).run(
        bars(["100", "110", "120"]), BuyAndHoldStrategy()
    )

    assert result.executions[0].timestamp == bars(["100", "110"])[1].timestamp
    assert result.executions[0].price == Decimal("110")
    assert result.final_equity == Decimal("109090.9090909090909090909091")
    assert result.metrics.net_return == pytest.approx(0.0909090909)


def test_costs_reduce_results_deterministically() -> None:
    market = bars(["100", "101", "102", "103"])
    free = BacktestEngine(BacktestConfig(fee_rate=Decimal("0"), slippage_bps=Decimal("0"))).run(
        market, BuyAndHoldStrategy()
    )
    costly_engine = BacktestEngine(
        BacktestConfig(fee_rate=Decimal("0.002"), slippage_bps=Decimal("10"))
    )
    first = costly_engine.run(market, BuyAndHoldStrategy())
    second = costly_engine.run(market, BuyAndHoldStrategy())

    assert first == second
    assert first.final_equity < free.final_equity
    assert first.metrics.total_fees > 0
    assert first.metrics.trade_count == 1


class _RecordingStrategy:
    def __init__(self) -> None:
        self.seen: list[tuple[datetime, tuple[datetime, ...]]] = []

    def reset(self) -> None:
        self.seen.clear()

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        self.seen.append((context.as_of, tuple(bar.timestamp for bar in context.bars)))
        return None


def test_context_never_exposes_future_bars() -> None:
    strategy = _RecordingStrategy()
    BacktestEngine().run(bars(["100", "101", "102"]), strategy)

    assert [len(history) for _, history in strategy.seen] == [1, 2, 3]
    assert all(max(history) == as_of for as_of, history in strategy.seen)


def test_prior_history_is_visible_but_not_scored_or_mixed_with_future() -> None:
    market = bars(["100", "101", "102", "103"])
    strategy = _RecordingStrategy()
    result = BacktestEngine().run(market[2:], strategy, history=market[:2])

    assert [len(history) for _, history in strategy.seen] == [2, 3, 4]
    assert all(max(history) == as_of for as_of, history in strategy.seen)
    assert tuple(point.timestamp for point in result.equity_curve) == tuple(
        bar.timestamp for bar in market[2:]
    )


class _StaleSignalStrategy:
    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal:
        return TargetSignal(Decimal("1"), context.as_of - timedelta(hours=1))


def test_stale_as_of_is_rejected() -> None:
    with pytest.raises(BacktestValidationError, match="as_of"):
        BacktestEngine().run(bars(["100", "101"]), _StaleSignalStrategy())


def test_unsorted_or_mixed_market_is_rejected() -> None:
    market = bars(["100", "101"])
    with pytest.raises(BacktestValidationError, match="chronological"):
        BacktestEngine().run(list(reversed(market)), BuyAndHoldStrategy())

    other = Bar(
        market[1].timestamp,
        "ETHUSDT",
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
    )
    with pytest.raises(BacktestValidationError, match="one symbol"):
        BacktestEngine().run([market[0], other], BuyAndHoldStrategy())


class _LeveragedLongStrategy:
    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        if context.current_target == 0:
            return TargetSignal(Decimal("20"), context.as_of, "leveraged entry")
        return None


class _LeveragedShortStrategy:
    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        if context.current_target == 0:
            return TargetSignal(Decimal("-20"), context.as_of, "leveraged short entry")
        return None


def test_bankruptcy_liquidates_position_and_prevents_future_recovery() -> None:
    market = bars(["100", "100", "200"])
    market[1] = Bar(
        timestamp=market[1].timestamp,
        symbol="BTCUSDT",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("94"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    engine = BacktestEngine(
        BacktestConfig(
            fee_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            max_abs_exposure=Decimal("20"),
        )
    )

    result = engine.run(market, _LeveragedLongStrategy())

    assert result.final_equity == Decimal("0")
    assert result.metrics.net_return == -1.0
    assert result.metrics.max_drawdown == 1.0
    assert len(result.equity_curve) == len(market)
    assert [point.equity for point in result.equity_curve[1:]] == [
        Decimal("0"),
        Decimal("0"),
    ]
    assert result.executions[-1].reason == "bankruptcy/liquidation"
    assert result.executions[-1].price == Decimal("95")
    assert len(result.trades) == 1


def test_backtest_configuration_rejects_exposure_above_platform_leverage_limit() -> None:
    with pytest.raises(ValueError, match="20x"):
        BacktestConfig(max_abs_exposure=Decimal("20.01"))


def test_short_bankruptcy_uses_adverse_open_after_gap_and_is_terminal() -> None:
    market = bars(["100", "100", "110", "50"])
    market[2] = Bar(
        timestamp=market[2].timestamp,
        symbol="BTCUSDT",
        open=Decimal("110"),
        high=Decimal("111"),
        low=Decimal("109"),
        close=Decimal("110"),
        volume=Decimal("1"),
    )
    engine = BacktestEngine(
        BacktestConfig(
            fee_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            max_abs_exposure=Decimal("20"),
        )
    )

    result = engine.run(market, _LeveragedShortStrategy())

    assert result.final_equity == Decimal("0")
    assert len(result.equity_curve) == len(market)
    assert result.executions[-1].reason == "bankruptcy/liquidation"
    assert result.executions[-1].price == Decimal("110")
    assert result.equity_curve[-1].equity == Decimal("0")
