from __future__ import annotations

from datetime import datetime, timedelta

from income_tg.models.explanation import explain_contributions
from income_tg.models.inference import ModelPrediction
from income_tg.signals.domain import (
    ActivePosition,
    MarketType,
    PositionDirection,
    SignalAction,
    SignalCandidate,
)


class SignalPolicy:
    def __init__(self, min_confidence: float = 0.70) -> None:
        if not 0 < min_confidence < 1:
            raise ValueError("min_confidence должна находиться между 0 и 1")
        self.min_confidence = min_confidence

    def create_candidate(
        self,
        *,
        instrument: str,
        market_type: MarketType,
        reference_price: float,
        horizon: str,
        prediction: ModelPrediction,
        current_position: ActivePosition | None = None,
        validity: timedelta = timedelta(minutes=15),
    ) -> SignalCandidate:
        if reference_price <= 0:
            raise ValueError("reference_price должна быть положительной")
        action = self._action(market_type, prediction, current_position)
        explanations = explain_contributions(prediction.contributions)
        if action is SignalAction.HOLD:
            explanations = ("уверенность модели ниже рабочего порога",)
        return SignalCandidate(
            instrument=instrument,
            market_type=market_type,
            action=action,
            confidence=prediction.confidence,
            reference_price=reference_price,
            horizon=horizon,
            as_of=prediction.as_of,
            valid_until=prediction.as_of + validity,
            model_version=prediction.model_version,
            reasons=explanations,
            cancel_condition=self._cancel_condition(action, reference_price),
        )

    def _action(
        self,
        market_type: MarketType,
        prediction: ModelPrediction,
        current_position: ActivePosition | None,
    ) -> SignalAction:
        bullish = (
            prediction.probability_up >= self.min_confidence
            and prediction.probability_up > prediction.probability_no_trade
        )
        bearish = (
            prediction.probability_down >= self.min_confidence
            and prediction.probability_down > prediction.probability_no_trade
        )
        if current_position is not None:
            if (
                current_position.direction in {PositionDirection.SPOT, PositionDirection.LONG}
                and bearish
            ):
                return SignalAction.CLOSE
            if current_position.direction is PositionDirection.SHORT and bullish:
                return SignalAction.CLOSE
            return SignalAction.HOLD
        if bullish:
            return SignalAction.BUY if market_type is MarketType.SPOT else SignalAction.LONG
        if bearish and market_type is MarketType.LINEAR_PERPETUAL:
            return SignalAction.SHORT
        return SignalAction.HOLD

    @staticmethod
    def _cancel_condition(action: SignalAction, reference_price: float) -> str:
        if action in {SignalAction.BUY, SignalAction.LONG}:
            return f"Отменить при подтвержденном снижении ниже {reference_price * 0.995:.8g}"
        if action is SignalAction.SHORT:
            return f"Отменить при подтвержденном росте выше {reference_price * 1.005:.8g}"
        if action is SignalAction.CLOSE:
            return "Сигнал действует до полного закрытия позиции или истечения срока"
        return "Действие не требуется"


def signal_is_expired(candidate: SignalCandidate, now: datetime) -> bool:
    return now >= candidate.valid_until
