"""Create owner portfolio ledger tables.

Revision ID: 20260810_0001
Revises:
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("base_currency", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "name", name="uq_portfolio_user_kind_name"),
    )
    op.create_table(
        "portfolio_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reverses_event_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reverses_event_id"], ["portfolio_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "portfolio_id", "idempotency_key", name="uq_portfolio_event_idempotency"
        ),
    )
    op.create_index(
        "ix_portfolio_events_portfolio_occurred",
        "portfolio_events",
        ["portfolio_id", "occurred_at"],
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("asset", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("entry_kind", sa.String(length=32), nullable=False),
        sa.CheckConstraint("amount <> 0", name="ck_ledger_entry_non_zero"),
        sa.ForeignKeyConstraint(["event_id"], ["portfolio_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_asset", "ledger_entries", ["asset"])

    # Financial events are append-only at the database level.
    op.execute(
        """
        CREATE FUNCTION prevent_ledger_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'portfolio ledger is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("portfolio_events", "ledger_entries"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
            """
        )


def downgrade() -> None:
    for table in ("ledger_entries", "portfolio_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_ledger_mutation()")
    op.drop_index("ix_ledger_entries_asset", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_portfolio_events_portfolio_occurred", table_name="portfolio_events")
    op.drop_table("portfolio_events")
    op.drop_table("portfolios")
    op.drop_table("users")
