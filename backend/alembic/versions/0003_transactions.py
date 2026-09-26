"""transactions

Revision ID: 0003_transactions
Revises: 0002_merchants_wallets
Create Date: 2025-01-06 10:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_transactions"
down_revision: str | None = "0002_merchants_wallets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALUES = ("pending", "completed", "failed", "reversed")


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("merchant_id", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        # native_enum=False keeps this a VARCHAR + CHECK constraint, so adding a
        # status later does not require an ALTER TYPE on a live database.
        sa.Column(
            "status",
            sa.Enum(
                *STATUS_VALUES,
                name="transaction_status",
                native_enum=False,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        # This constraint is what makes a retried payment safe: the database,
        # not an application-level "check then insert", breaks the tie.
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )
    op.create_index(
        op.f("ix_transactions_merchant_id"), "transactions", ["merchant_id"], unique=False
    )
    op.create_index(
        op.f("ix_transactions_wallet_id"), "transactions", ["wallet_id"], unique=False
    )
    # Supports the settlement job's "completed transactions for this merchant
    # on this date that are not yet in a batch" scan.
    op.create_index(
        "ix_transactions_merchant_status_created",
        "transactions",
        ["merchant_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_merchant_status_created", table_name="transactions")
    op.drop_index(op.f("ix_transactions_wallet_id"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_merchant_id"), table_name="transactions")
    op.drop_table("transactions")
