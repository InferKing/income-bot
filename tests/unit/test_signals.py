from datetime import UTC, datetime, timedelta

from income_tg.models.inference import ModelPrediction
from income_tg.signals.deduplication import SignalDeduplicator
from income_tg.signals.domain import (
    ActivePosition,
    MarketType,
    PositionDirection,
    SignalAction,
)
from income_tg.signals.policy import SignalPolicy
from income_tg.signals.urgent import UrgentEventType, position_alerts


def _prediction(up: float) -> ModelPrediction:
    return ModelPrediction(
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        probability_up=up,
        probability_down=1 - up,
        probability_no_trade=0.0,
        confidence=max(up, 1 - up),
        expected_directional_score=up - 0.5,
        contributions=(("return_5", 1.2), ("funding_rate", -0.2)),
        model_version="model-1",
    )


def test_policy_creates_long_and_closes_on_reversal() -> None:
    policy = SignalPolicy()
    long_signal = policy.create_candidate(
        instrument="BTCUSDT",
        market_type=MarketType.LINEAR_PERPETUAL,
        reference_price=100,
        horizon="1h",
        prediction=_prediction(0.8),
    )
    assert long_signal.action is SignalAction.LONG
    close_signal = policy.create_candidate(
        instrument="BTCUSDT",
        market_type=MarketType.LINEAR_PERPETUAL,
        reference_price=95,
        horizon="1h",
        prediction=_prediction(0.2),
        current_position=ActivePosition(PositionDirection.LONG),
    )
    assert close_signal.action is SignalAction.CLOSE


def test_policy_holds_when_no_trade_probability_dominates() -> None:
    prediction = _prediction(0.6)
    prediction = ModelPrediction(
        as_of=prediction.as_of,
        probability_up=0.2,
        probability_down=0.1,
        probability_no_trade=0.7,
        confidence=0.2,
        expected_directional_score=0.1,
        contributions=prediction.contributions,
        model_version=prediction.model_version,
    )
    candidate = SignalPolicy(0.15).create_candidate(
        instrument="BTCUSDT",
        market_type=MarketType.LINEAR_PERPETUAL,
        reference_price=100,
        horizon="15m",
        prediction=prediction,
    )
    assert candidate.action is SignalAction.HOLD


def test_deduplicator_uses_cooldown() -> None:
    candidate = SignalPolicy().create_candidate(
        instrument="ETHUSDT",
        market_type=MarketType.SPOT,
        reference_price=100,
        horizon="15m",
        prediction=_prediction(0.8),
    )
    deduplicator = SignalDeduplicator(cooldown=timedelta(minutes=15))
    assert deduplicator.accept(candidate) is True
    assert deduplicator.accept(candidate) is False


def test_position_alerts_detect_stop_and_liquidation_proximity() -> None:
    alerts = position_alerts(
        ActivePosition(
            direction=PositionDirection.LONG,
            stop_loss=99.5,
            liquidation_price=99.2,
        ),
        100,
    )
    assert {item.event_type for item in alerts} == {
        UrgentEventType.STOP_APPROACHING,
        UrgentEventType.LIQUIDATION_APPROACHING,
    }
