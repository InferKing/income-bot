"""Freshness, continuity, cross-source and provider-health checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from income_tg.market_data.schemas import AdapterHealth, Candle, DataSource


class MarketDataQualityError(RuntimeError):
    pass


class StaleMarketDataError(MarketDataQualityError):
    pass


class PriceDivergenceError(MarketDataQualityError):
    pass


@dataclass(frozen=True, slots=True)
class CandleGap:
    expected_at: datetime
    next_seen_at: datetime | None


def find_candle_gaps(
    candles: list[Candle], start: datetime, end: datetime, interval_seconds: int
) -> list[CandleGap]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    seen = {candle.opened_at for candle in candles}
    gaps: list[CandleGap] = []
    cursor = start
    step = timedelta(seconds=interval_seconds)
    ordered = sorted(seen)
    while cursor < end:
        if cursor not in seen:
            next_seen = next((item for item in ordered if item > cursor), None)
            gaps.append(CandleGap(expected_at=cursor, next_seen_at=next_seen))
        cursor += step
    return gaps


def assert_fresh(
    observed_at: datetime,
    *,
    maximum_age: timedelta,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    age = current - observed_at
    if age > maximum_age or age < timedelta(seconds=-5):
        raise StaleMarketDataError(f"market data age {age} is outside allowed range")


def assert_prices_close(
    primary: Decimal,
    reserve: Decimal,
    *,
    maximum_relative_difference: Decimal,
) -> None:
    if primary <= 0 or reserve <= 0:
        raise ValueError("prices must be positive")
    relative = abs(primary - reserve) / ((primary + reserve) / 2)
    if relative > maximum_relative_difference:
        raise PriceDivergenceError(
            f"provider prices differ by {relative:.4%}; limit is {maximum_relative_difference:.4%}"
        )


@dataclass(slots=True)
class ProviderStatus:
    source: DataSource
    healthy: bool = False
    consecutive_failures: int = 0
    last_checked_at: datetime | None = None
    last_error: str | None = None

    def observe(self, health: AdapterHealth) -> bool:
        """Update status and return True when an alert-worthy state transition occurs."""
        if health.source is not self.source:
            raise ValueError("health result belongs to another provider")
        was_healthy = self.healthy
        self.healthy = health.healthy
        self.last_checked_at = health.checked_at
        self.last_error = health.detail
        self.consecutive_failures = 0 if health.healthy else self.consecutive_failures + 1
        return was_healthy != self.healthy
