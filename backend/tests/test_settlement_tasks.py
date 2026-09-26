"""Celery settlement tasks.

The tasks open their own session by design -- a Celery worker has no request
scope to inherit one from. In tests, ``SessionLocal`` is swapped for a shim
around the rolled-back test session, so exercising a task cannot leak rows into
the development database.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.factories import make_merchant, make_transaction, make_wallet

from app.models import SettlementBatch
from app.tasks import settlement as settlement_task

DAY_ONE = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)


class _SessionShim:
    """Delegates everything to the test session but tolerates close()."""

    def __init__(self, session) -> None:
        self._session = session

    def close(self) -> None:
        """No-op: the test fixture owns the session lifecycle."""

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest.fixture
def celery_task_db(monkeypatch, db):
    monkeypatch.setattr(settlement_task, "SessionLocal", lambda: _SessionShim(db))
    return db


@pytest.fixture
def seed(db):
    """Seed one completed transaction on DAY_ONE for the given merchant."""

    def _seed(merchant, amount: str):
        wallet = make_wallet(db)
        return make_transaction(
            db,
            merchant_id=merchant.id,
            wallet_id=wallet.id,
            amount=amount,
            created_at=DAY_ONE,
        )

    return _seed


def test_task_settles_a_merchant(celery_task_db, seed) -> None:
    merchant = make_merchant(celery_task_db, commission_rate=Decimal("0.025"))
    seed(merchant, "400.00")

    result = settlement_task.settle_merchant(merchant.id, DAY_ONE.date().isoformat())

    assert result["status"] == "settled"
    assert result["batch_id"] is not None
    assert result["transaction_count"] == 1
    assert result["total_amount"] == "400.00"
    assert result["commission_deducted"] == "10.00"
    assert result["net_payout"] == "390.00"
    assert result["batch_date"] == DAY_ONE.date().isoformat()


def test_task_is_idempotent(celery_task_db, seed) -> None:
    merchant = make_merchant(celery_task_db)
    seed(merchant, "100.00")
    day = DAY_ONE.date().isoformat()

    first = settlement_task.settle_merchant(merchant.id, day)
    second = settlement_task.settle_merchant(merchant.id, day)

    assert first["status"] == "settled"
    assert second["status"] == "nothing_to_settle"
    assert second["batch_id"] is None
    assert len(celery_task_db.scalars(select(SettlementBatch)).all()) == 1


def test_task_reports_an_unknown_merchant(celery_task_db) -> None:
    result = settlement_task.settle_merchant(999_999, DAY_ONE.date().isoformat())
    assert result["status"] == "unknown_merchant"


def test_task_defaults_to_yesterday_and_ignores_older_activity(celery_task_db, seed) -> None:
    """The nightly run settles yesterday, so a stale back-dated row is not picked up."""
    merchant = make_merchant(celery_task_db)
    seed(merchant, "100.00")

    result = settlement_task.settle_merchant(merchant.id)

    assert result["status"] == "nothing_to_settle"


def test_nightly_task_settles_every_merchant(celery_task_db, seed) -> None:
    day = DAY_ONE.date().isoformat()
    first = make_merchant(celery_task_db, email="nightly-a@example.com")
    second = make_merchant(celery_task_db, email="nightly-b@example.com")
    quiet = make_merchant(celery_task_db, email="nightly-c@example.com")
    seed(first, "100.00")
    seed(second, "300.00")

    result = settlement_task.settle_all_merchants(day)

    assert result["batch_date"] == day
    assert result["merchants_examined"] == 3
    assert result["batches_created"] == 2
    assert result["nothing_to_settle"] == [quiet.id]
    assert result["errors"] == []
    # 100.00 and 300.00 at the default 2% rate -> 98.00 and 294.00 net.
    net = sum(Decimal(item["net_payout"]) for item in result["settled"])
    assert net == Decimal("392.00")
