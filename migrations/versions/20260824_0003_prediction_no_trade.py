"""store NO TRADE probability for three-class model predictions

Revision ID: 20260824_0003
Revises: 20260810_0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0003"
down_revision: str | None = "20260810_0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("probability_no_trade", sa.Float(), nullable=False, server_default="0"),
    )
    op.alter_column("predictions", "probability_no_trade", server_default=None)


def downgrade() -> None:
    op.drop_column("predictions", "probability_no_trade")
