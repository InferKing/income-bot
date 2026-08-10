from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Yekaterinburg")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    portfolios: Mapped[list[Portfolio]] = relationship(back_populates="user")


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "name", name="uq_portfolio_user_kind_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False, default="RUB")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="portfolios")
    events: Mapped[list[PortfolioEvent]] = relationship(back_populates="portfolio")


class PortfolioEvent(Base):
    __tablename__ = "portfolio_events"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "idempotency_key", name="uq_portfolio_event_idempotency"),
        Index("ix_portfolio_events_portfolio_occurred", "portfolio_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reverses_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("portfolio_events.id", ondelete="RESTRICT")
    )
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    portfolio: Mapped[Portfolio] = relationship(back_populates="events")
    entries: Mapped[list[LedgerEntry]] = relationship(
        back_populates="event", order_by="LedgerEntry.id"
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_ledger_entry_non_zero"),
        Index("ix_ledger_entries_asset", "asset"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolio_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="PRINCIPAL")

    event: Mapped[PortfolioEvent] = relationship(back_populates="entries")
