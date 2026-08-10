"""Create market, model, signal and operations tables.

Revision ID: 20260810_0002
Revises: 20260810_0001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0002"
down_revision: str | None = "20260810_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("base_asset", sa.String(16), nullable=False),
        sa.Column("quote_asset", sa.String(16), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_symbol", "market_type", name="uq_instrument_symbol_market"),
    )
    op.create_table(
        "market_candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(38, 18), nullable=False),
        sa.Column("high", sa.Numeric(38, 18), nullable=False),
        sa.Column("low", sa.Numeric(38, 18), nullable=False),
        sa.Column("close", sa.Numeric(38, 18), nullable=False),
        sa.Column("volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("turnover", sa.Numeric(38, 18)),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "instrument_id", "interval_seconds", "opened_at", name="uq_market_candle"
        ),
    )
    op.create_index(
        "ix_market_candle_lookup",
        "market_candles",
        ["instrument_id", "interval_seconds", "opened_at"],
    )
    op.create_table(
        "market_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("provider_trade_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "instrument_id", "provider_trade_id", name="uq_market_trade"
        ),
    )
    op.create_index("ix_market_trade_lookup", "market_trades", ["instrument_id", "occurred_at"])
    op.create_table(
        "orderbook_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("bids", sa.JSON(), nullable=False),
        sa.Column("asks", sa.JSON(), nullable=False),
        sa.Column("best_bid", sa.Numeric(38, 18), nullable=False),
        sa.Column("best_ask", sa.Numeric(38, 18), nullable=False),
        sa.Column("spread_bps", sa.Float(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orderbook_lookup", "orderbook_snapshots", ["instrument_id", "captured_at"])
    op.create_table(
        "derivatives_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Numeric(24, 18)),
        sa.Column("open_interest", sa.Numeric(38, 18)),
        sa.Column("mark_price", sa.Numeric(38, 18)),
        sa.Column("index_price", sa.Numeric(38, 18)),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "instrument_id", "observed_at", name="uq_derivative_metric"
        ),
    )
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("base", sa.String(16), nullable=False),
        sa.Column("quote", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rate", sa.Numeric(38, 18), nullable=False),
        sa.Column("is_derived", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base", "quote", "provider", "observed_at", name="uq_fx_rate"),
    )
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Uuid()),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "risk_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("max_margin_fraction", sa.Numeric(8, 6), nullable=False),
        sa.Column("max_stop_risk_fraction", sa.Numeric(8, 6), nullable=False),
        sa.Column("max_daily_loss_fraction", sa.Numeric(8, 6), nullable=False),
        sa.Column("max_drawdown_fraction", sa.Numeric(8, 6), nullable=False),
        sa.Column("max_open_positions", sa.Integer(), nullable=False),
        sa.Column("max_leverage", sa.Integer(), nullable=False),
        sa.Column("min_signal_confidence", sa.Numeric(8, 6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "settings_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("setting_name", sa.String(64), nullable=False),
        sa.Column("old_value", sa.String(128), nullable=False),
        sa.Column("new_value", sa.String(128), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("source", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "training_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("train_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON()),
        sa.Column("error_message", sa.Text()),
        sa.Column("code_version", sa.String(128), nullable=False),
        sa.Column("data_version", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("training_run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.String(128), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["training_run_id"], ["training_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "predictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("probability_up", sa.Float(), nullable=False),
        sa.Column("probability_down", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("contributions", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prediction_lookup", "predictions", ["instrument_id", "as_of"])
    op.create_table(
        "feature_vectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_hash", sa.String(128), nullable=False),
        sa.Column("names", sa.JSON(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id", "horizon", "as_of", "schema_hash", name="uq_feature_vector"
        ),
    )
    op.create_index(
        "ix_feature_vector_lookup",
        "feature_vectors",
        ["instrument_id", "horizon", "as_of"],
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("prediction_id", sa.Uuid()),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reference_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18)),
        sa.Column("margin", sa.Numeric(38, 18)),
        sa.Column("leverage", sa.Integer()),
        sa.Column("stop_loss", sa.Numeric(38, 18)),
        sa.Column("take_profit", sa.Numeric(38, 18)),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("risk_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signal_status_created", "signals", ["status", "created_at"])
    op.create_table(
        "risk_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("calculated_values", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid()),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("limit_price", sa.Numeric(38, 18)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("reference_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("fill_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee", sa.Numeric(38, 18), nullable=False),
        sa.Column("slippage_bps", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("position_key", sa.String(160), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("margin", sa.Numeric(38, 18), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 18), nullable=False),
        sa.Column("take_profit", sa.Numeric(38, 18), nullable=False),
        sa.Column("opening_commission", sa.Numeric(38, 18), nullable=False),
        sa.Column("funding_pnl", sa.Numeric(38, 18), nullable=False),
        sa.Column("last_funding_at", sa.DateTime(timezone=True)),
        sa.Column("liquidation_price", sa.Numeric(38, 18)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_key", name="uq_paper_position_key"),
    )
    op.create_index("ix_paper_position_open", "paper_positions", ["portfolio_id", "status"])
    op.create_table(
        "equity_curve",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity_usdt", sa.Numeric(38, 18), nullable=False),
        sa.Column("equity_rub", sa.Numeric(38, 18), nullable=False),
        sa.Column("drawdown_fraction", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "observed_at", name="uq_equity_point"),
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("deduplication_key", sa.String(128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("deduplication_key", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_table(
        "service_health",
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("service", "instance_id"),
    )


def downgrade() -> None:
    op.drop_table("service_health")
    op.drop_table("scheduled_jobs")
    op.drop_table("notification_outbox")
    op.drop_table("equity_curve")
    op.drop_index("ix_paper_position_open", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_table("paper_fills")
    op.drop_table("paper_orders")
    op.drop_table("risk_decisions")
    op.drop_index("ix_signal_status_created", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_feature_vector_lookup", table_name="feature_vectors")
    op.drop_table("feature_vectors")
    op.drop_index("ix_prediction_lookup", table_name="predictions")
    op.drop_table("predictions")
    op.drop_table("model_versions")
    op.drop_table("training_runs")
    op.drop_table("settings_audit")
    op.drop_table("risk_profiles")
    op.drop_table("data_quality_events")
    op.drop_table("fx_rates")
    op.drop_table("derivatives_metrics")
    op.drop_index("ix_orderbook_lookup", table_name="orderbook_snapshots")
    op.drop_table("orderbook_snapshots")
    op.drop_index("ix_market_trade_lookup", table_name="market_trades")
    op.drop_table("market_trades")
    op.drop_index("ix_market_candle_lookup", table_name="market_candles")
    op.drop_table("market_candles")
    op.drop_table("instruments")
