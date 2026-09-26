"""settlement batches and items

Revision ID: 0004_settlements
Revises: 0003_transactions
Create Date: 2025-01-07 10:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_settlements"
down_revision: str | None = "0003_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTLEMENT_STATUS_VALUES = ("pending", "completed", "failed")


def upgrade() -> None:
    op.create_table(
        "settlement_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("merchant_id", sa.Integer(), nullable=False),
        sa.Column("batch_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("commission_deducted", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("net_payout", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *SETTLEMENT_STATUS_VALUES,
                name="settlement_status",
                native_enum=False,
                length=16,
            ),
            server_default="completed",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Serves both the reconciliation report and the dashboard's history view.
    op.create_index(
        "ix_settlement_batches_merchant_date",
        "settlement_batches",
        ["merchant_id", "batch_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_settlement_batches_merchant_id"),
        "settlement_batches",
        ["merchant_id"],
        unique=False,
    )

    op.create_table(
        "settlement_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["settlement_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Makes "settled at most once" a database guarantee, not a convention.
        sa.UniqueConstraint("transaction_id", name="uq_settlement_items_transaction"),
    )
    op.create_index(
        op.f("ix_settlement_items_batch_id"),
        "settlement_items",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_settlement_items_transaction_id"),
        "settlement_items",
        ["transaction_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_settlement_items_transaction_id"), table_name="settlement_items"
    )
    op.drop_index(op.f("ix_settlement_items_batch_id"), table_name="settlement_items")
    op.drop_table("settlement_items")
    op.drop_index(op.f("ix_settlement_batches_merchant_id"), table_name="settlement_batches")
    op.drop_index("ix_settlement_batches_merchant_date", table_name="settlement_batches")
    op.drop_table("settlement_batches")
