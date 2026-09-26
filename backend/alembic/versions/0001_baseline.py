"""baseline: stamp alembic version table

This migration intentionally creates no domain tables. It establishes the
`alembic_version` bookkeeping row so that later revisions have a parent to
chain onto, and so `alembic current` reports a known state on a fresh
database.

Revision ID: 0001_baseline
Revises:
Create Date: 2025-01-06 10:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
