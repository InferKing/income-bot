from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PortfolioBalance:
    portfolio_id: UUID
    name: str
    kind: str
    balances: dict[str, Decimal]
