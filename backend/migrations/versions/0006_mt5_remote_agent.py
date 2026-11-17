"""Add tenant-scoped Windows MT5 agents and durable commands."""

from alembic import op
import sqlalchemy as sa


revision = "0006_mt5_remote_agent"
down_revision = "0005_durable_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mt5_agents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trade_mode", sa.String(length=16), nullable=True),
        sa.Column("account_currency", sa.String(length=8), nullable=True),
        sa.Column("account_equity", sa.Float(), nullable=True),
        sa.Column("terminal_version", sa.String(length=64), nullable=True),
        sa.Column("agent_version", sa.String(length=32), nullable=True),
        sa.Column("account_metadata", sa.JSON(), nullable=False),
        sa.Column("allow_live_orders", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["broker_account_id"], ["broker_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broker_account_id", name="uq_mt5_agents_broker_account_id"),
        sa.UniqueConstraint("token_hash", name="uq_mt5_agents_token_hash"),
    )
    op.create_index("ix_mt5_agents_tenant_id", "mt5_agents", ["tenant_id"])
    op.create_index("ix_mt5_agents_broker_account_id", "mt5_agents", ["broker_account_id"])

    op.create_table(
        "mt5_agent_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["mt5_agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_mt5_agent_commands_idempotency_key"),
    )
    op.create_index("ix_mt5_agent_commands_tenant_id", "mt5_agent_commands", ["tenant_id"])
    op.create_index("ix_mt5_agent_commands_agent_id", "mt5_agent_commands", ["agent_id"])
    op.create_index("ix_mt5_agent_commands_status", "mt5_agent_commands", ["status"])
    op.create_index("ix_mt5_agent_commands_lease_until", "mt5_agent_commands", ["lease_until"])
    op.create_index("ix_mt5_agent_commands_expires_at", "mt5_agent_commands", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_mt5_agent_commands_expires_at", table_name="mt5_agent_commands")
    op.drop_index("ix_mt5_agent_commands_lease_until", table_name="mt5_agent_commands")
    op.drop_index("ix_mt5_agent_commands_status", table_name="mt5_agent_commands")
    op.drop_index("ix_mt5_agent_commands_agent_id", table_name="mt5_agent_commands")
    op.drop_index("ix_mt5_agent_commands_tenant_id", table_name="mt5_agent_commands")
    op.drop_table("mt5_agent_commands")
    op.drop_index("ix_mt5_agents_broker_account_id", table_name="mt5_agents")
    op.drop_index("ix_mt5_agents_tenant_id", table_name="mt5_agents")
    op.drop_table("mt5_agents")
