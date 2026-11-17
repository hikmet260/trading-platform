"""Add idempotency fields and configurable portfolio risk limits.

Revision ID: 0004_runtime_hardening
Revises: 0003_system_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_runtime_hardening"
down_revision = "0003_system_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trade_logs", sa.Column("client_order_id", sa.String(length=80), nullable=True))
    op.add_column("trade_logs", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_trade_logs_client_order_id", "trade_logs", ["client_order_id"], unique=True)
    op.create_index("ix_trade_logs_expires_at", "trade_logs", ["expires_at"], unique=False)

    op.add_column("system_settings", sa.Column("account_currency", sa.String(length=8), nullable=False, server_default="USD"))
    op.add_column("system_settings", sa.Column("max_trade_risk_pct", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("system_settings", sa.Column("max_open_risk_pct", sa.Float(), nullable=False, server_default="2.0"))
    op.add_column("system_settings", sa.Column("max_notional_multiple", sa.Float(), nullable=False, server_default="3.0"))
    op.add_column("system_settings", sa.Column("max_positions_per_tenant", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("system_settings", sa.Column("max_daily_trades", sa.Integer(), nullable=False, server_default="20"))
    op.add_column("system_settings", sa.Column("proposal_ttl_seconds", sa.Integer(), nullable=False, server_default="900"))


def downgrade() -> None:
    op.drop_column("system_settings", "proposal_ttl_seconds")
    op.drop_column("system_settings", "max_daily_trades")
    op.drop_column("system_settings", "max_positions_per_tenant")
    op.drop_column("system_settings", "max_notional_multiple")
    op.drop_column("system_settings", "max_open_risk_pct")
    op.drop_column("system_settings", "max_trade_risk_pct")
    op.drop_column("system_settings", "account_currency")

    op.drop_index("ix_trade_logs_expires_at", table_name="trade_logs")
    op.drop_index("ix_trade_logs_client_order_id", table_name="trade_logs")
    op.drop_column("trade_logs", "expires_at")
    op.drop_column("trade_logs", "client_order_id")
