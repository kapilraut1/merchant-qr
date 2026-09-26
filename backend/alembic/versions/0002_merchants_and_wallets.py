"""merchants and wallets

Revision ID: 0002_merchants_wallets
Revises: 0001_baseline
Create Date: 2025-01-06 10:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_merchants_wallets"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        # Platform commission as a fraction: 0.02 == 2%.
        sa.Column(
            "commission_rate",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            server_default=sa.text("0.0200"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # The unique index (not just the column constraint) is what lets the
    # application translate a concurrent duplicate registration into a 409.
    op.create_index(op.f("ix_merchants_email"), "merchants", ["email"], unique=True)

    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.String(length=120), nullable=False),
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column(
            "balance",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NPR"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallets_customer_id"), "wallets", ["customer_id"], unique=False)
    op.create_index(op.f("ix_wallets_merchant_id"), "wallets", ["merchant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_wallets_merchant_id"), table_name="wallets")
    op.drop_index(op.f("ix_wallets_customer_id"), table_name="wallets")
    op.drop_table("wallets")
    op.drop_index(op.f("ix_merchants_email"), table_name="merchants")
    op.drop_table("merchants")
