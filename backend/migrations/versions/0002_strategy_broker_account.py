"""Allow strategies to bind to a tenant-owned execution account.

Revision ID: 0002_strategy_broker_account
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_strategy_broker_account"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategies", sa.Column("broker_account_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_strategies_broker_account_id_broker_accounts", "strategies", "broker_accounts",
                          ["broker_account_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_strategies_broker_account_id", "strategies", ["broker_account_id"])


def downgrade() -> None:
    op.drop_index("ix_strategies_broker_account_id", table_name="strategies")
    op.drop_constraint("fk_strategies_broker_account_id_broker_accounts", "strategies", type_="foreignkey")
    op.drop_column("strategies", "broker_account_id")
