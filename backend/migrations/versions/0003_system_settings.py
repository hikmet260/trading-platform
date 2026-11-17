"""Add system_settings table for tenant-configurable AI, Telegram, and Risk parameters.

Revision ID: 0003_system_settings
Revises: 0002_strategy_broker_account
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_system_settings"
down_revision = "0002_strategy_broker_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("tenant_id", sa.String(length=36), primary_key=True),
        sa.Column("ai_provider", sa.String(length=40), nullable=False, server_default="openai"),
        sa.Column("ai_model", sa.String(length=80), nullable=False, server_default="gpt-4o"),
        sa.Column("encrypted_ai_api_key", sa.Text(), nullable=True),
        sa.Column("ai_temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("ai_system_prompt", sa.Text(), nullable=True),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_allowed_user_ids", sa.String(length=255), nullable=True),
        sa.Column("telegram_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("encrypted_telegram_bot_token", sa.Text(), nullable=True),
        sa.Column("execution_mode", sa.String(length=20), nullable=False, server_default="semi_auto"),
        sa.Column("max_daily_drawdown_pct", sa.Float(), nullable=False, server_default="3.0"),
        sa.Column("news_blackout_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("paper_account_equity", sa.Float(), nullable=False, server_default="10000.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
