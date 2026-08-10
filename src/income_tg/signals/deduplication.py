from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from income_tg.signals.domain import SignalCandidate


@dataclass(slots=True)
class SignalDeduplicator:
    cooldown: timedelta = timedelta(minutes=15)
    _seen: dict[str, datetime] = field(default_factory=dict)

    def accept(self, candidate: SignalCandidate) -> bool:
        key = self.fingerprint(candidate)
        previous = self._seen.get(key)
        if previous is not None and candidate.as_of - previous < self.cooldown:
            return False
        self._seen[key] = candidate.as_of
        self._evict(candidate.as_of)
        return True

    @staticmethod
    def fingerprint(candidate: SignalCandidate) -> str:
        return ":".join(
            (
                candidate.instrument,
                candidate.market_type.value,
                candidate.action.value,
                candidate.horizon,
                candidate.model_version,
            )
        )

    def _evict(self, now: datetime) -> None:
        threshold = now - self.cooldown * 4
        self._seen = {key: value for key, value in self._seen.items() if value >= threshold}
