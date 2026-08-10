from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.time import utc_now
from income_tg.notifications.outbox import NotificationOutbox
from income_tg.risk.models import RiskDecision
from income_tg.signals.domain import SignalAction, SignalCandidate
from income_tg.storage.trading_models import RiskDecisionRecord, SignalRecord


class SignalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        user_id: UUID,
        portfolio_id: UUID,
        instrument_id: UUID,
        candidate: SignalCandidate,
        risk_decision: RiskDecision | None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        prediction_id: UUID | None = None,
        now: datetime | None = None,
    ) -> SignalRecord:
        if candidate.valid_until.tzinfo is None or candidate.valid_until.utcoffset() is None:
            raise ValueError("signal valid_until must be timezone-aware")
        if candidate.valid_until <= (now or utc_now()):
            raise ValueError("expired signal cannot be recorded")
        requires_sizing = candidate.action in {
            SignalAction.BUY,
            SignalAction.LONG,
            SignalAction.SHORT,
        }
        if requires_sizing and risk_decision is None:
            raise ValueError("Входной сигнал требует решения риск-модуля")
        sizing = risk_decision.sizing if risk_decision is not None else None
        approved = not requires_sizing or sizing is not None
        record = SignalRecord(
            portfolio_id=portfolio_id,
            instrument_id=instrument_id,
            prediction_id=prediction_id,
            action=candidate.action.value,
            status="APPROVED" if approved else "REJECTED",
            confidence=candidate.confidence,
            reference_price=Decimal(str(candidate.reference_price)),
            quantity=sizing.quantity if sizing else None,
            margin=sizing.margin if sizing else None,
            leverage=sizing.leverage if sizing else None,
            stop_loss=stop_loss,
            take_profit=take_profit,
            horizon=candidate.horizon,
            valid_until=candidate.valid_until,
            explanation=list(candidate.reasons),
            risk_snapshot={
                "reasons": [reason.value for reason in risk_decision.reasons]
                if risk_decision
                else [],
                "stop_loss_amount": str(sizing.stop_loss_amount) if sizing else None,
            },
        )
        self.session.add(record)
        await self.session.flush()
        if risk_decision is not None:
            self.session.add(
                RiskDecisionRecord(
                    signal_id=record.id,
                    decision="APPROVED" if risk_decision.approved else "REJECTED",
                    reason_codes=[reason.value for reason in risk_decision.reasons],
                    calculated_values=(
                        {
                            "quantity": str(sizing.quantity),
                            "notional": str(sizing.notional),
                            "margin": str(sizing.margin),
                            "leverage": sizing.leverage,
                        }
                        if sizing
                        else {}
                    ),
                )
            )
        if approved and candidate.action is not SignalAction.HOLD:
            await NotificationOutbox(self.session).enqueue(
                user_id=user_id,
                event_type="SIGNAL",
                deduplication_key=f"signal:{record.id}",
                priority=10 if candidate.action is SignalAction.CLOSE else 5,
                payload={"text": _render_signal(record, candidate)},
            )
        await self.session.flush()
        return record


def _render_signal(record: SignalRecord, candidate: SignalCandidate) -> str:
    lines = [
        f"<b>{candidate.instrument} — {candidate.action.value}</b>",
        f"Уверенность: {candidate.confidence:.1%}",
        f"Ориентир цены: <code>{candidate.reference_price:.8g}</code>",
    ]
    if record.quantity is not None:
        lines.append(f"Количество: <code>{record.quantity}</code>")
    if record.leverage is not None:
        lines.append(f"Плечо: {record.leverage}x")
    if record.stop_loss is not None:
        lines.append(f"Стоп-лосс: <code>{record.stop_loss}</code>")
    if record.take_profit is not None:
        lines.append(f"Тейк-профит: <code>{record.take_profit}</code>")
    lines.append("\nПричины:")
    lines.extend(f"• {reason}" for reason in candidate.reasons)
    lines.append(f"\n{candidate.cancel_condition}")
    return "\n".join(lines)
