"""Guards for the test harness itself.

Every other test relies on conftest rolling back its transaction. If a change to
conftest (or to SQLAlchemy's transaction-join semantics) ever let a committed
row escape, the rest of the suite would fail in confusing, order-dependent
ways. These two tests fail loudly and immediately instead.

They depend on pytest running tests in definition order within a module, which
is guaranteed.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Merchant


def test_a_commits_a_merchant(client: TestClient) -> None:
    """Runs first: commit a row through the application's own commit path."""
    response = client.post(
        "/merchants/register",
        json={
            "name": "Leak Probe",
            "email": "leak-probe@example.com",
            "password": "sup3r-secret-pw",
        },
    )
    assert response.status_code == 201, response.text


def test_b_sees_a_clean_table(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Runs second: the row committed by test_a must not be visible here."""
    count = db.scalar(select(func.count()).select_from(Merchant))
    assert count == 0, f"conftest leaked {count} row(s) between tests"


def test_committed_row_is_visible_within_the_same_test(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """The counter-test: within a single test, commits must be visible."""
    response = client.post(
        "/merchants/register",
        json={
            "name": "Visible Probe",
            "email": "visible-probe@example.com",
            "password": "sup3r-secret-pw",
        },
    )
    assert response.status_code == 201, response.text

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Merchant)) == 1

    merchant = db.scalar(select(Merchant))
    assert merchant is not None
    # The password must never be persisted in the clear.
    assert "sup3r-secret-pw" not in merchant.password_hash
    assert merchant.password_hash.startswith("pbkdf2_sha256$")
    assert Decimal(merchant.commission_rate) == Decimal("0.02")
