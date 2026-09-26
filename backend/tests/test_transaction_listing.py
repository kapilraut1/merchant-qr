"""Merchant transaction listing.

This endpoint exists for the dashboard: one call returns the day's rows and the
day's takings, so the screen cannot show a total that disagrees with its list.
"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.factories import (
    auth_header,
    make_transaction,
    make_wallet,
    register_merchant,
)

DAY_ONE = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
DAY_TWO = datetime(2025, 3, 2, 10, 0, tzinfo=UTC)


def _seed(db, merchant_id, wallet_id, amounts, when=DAY_ONE, status=None):
    rows = []
    for index, amount in enumerate(amounts):
        rows.append(
            make_transaction(
                db,
                merchant_id=merchant_id,
                wallet_id=wallet_id,
                amount=amount,
                status=status or "completed",
                created_at=when,
                idempotency_key=f"list-{when.date()}-{index}-{amount}",
            )
        )
    return rows


def test_lists_transactions_for_the_requested_day(client, db) -> None:
    registration = register_merchant(client, email="list-day@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    _seed(db, merchant_id, wallet.id, ["100.00", "250.00"], when=DAY_ONE)
    _seed(db, merchant_id, wallet.id, ["999.00"], when=DAY_TWO)

    response = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-01",
        headers=auth_header(registration),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == "2025-03-01"
    assert body["count"] == 2
    assert body["total_amount"] == "350.00"
    assert {t["amount"] for t in body["transactions"]} == {"100.00", "250.00"}


def test_list_is_newest_first(client, db) -> None:
    registration = register_merchant(client, email="list-order@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db,
        merchant_id=merchant_id,
        wallet_id=wallet.id,
        amount="10.00",
        created_at=datetime(2025, 3, 1, 9, 0, tzinfo=UTC),
        idempotency_key="older",
    )
    make_transaction(
        db,
        merchant_id=merchant_id,
        wallet_id=wallet.id,
        amount="20.00",
        created_at=datetime(2025, 3, 1, 18, 0, tzinfo=UTC),
        idempotency_key="newer",
    )

    response = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-01",
        headers=auth_header(registration),
    )

    amounts = [t["amount"] for t in response.json()["transactions"]]
    assert amounts == ["20.00", "10.00"]


def test_total_counts_only_completed_payments(client, db) -> None:
    """Failed attempts are listed but must not inflate "collected"."""
    from app.models import TransactionStatus

    registration = register_merchant(client, email="list-total@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    _seed(db, merchant_id, wallet.id, ["100.00"], status=TransactionStatus.COMPLETED)
    make_transaction(
        db,
        merchant_id=merchant_id,
        wallet_id=wallet.id,
        amount="5000.00",
        status=TransactionStatus.FAILED,
        created_at=DAY_ONE,
        idempotency_key="failed-one",
    )

    response = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-01",
        headers=auth_header(registration),
    )

    body = response.json()
    assert body["count"] == 2
    assert body["total_amount"] == "100.00"
    assert {t["status"] for t in body["transactions"]} == {"completed", "failed"}


def test_list_is_scoped_to_the_merchant(client, db) -> None:
    mine = register_merchant(client, email="list-mine@example.com")
    other = register_merchant(client, email="list-other@example.com")
    wallet = make_wallet(db)
    _seed(db, mine["merchant"]["id"], wallet.id, ["300.00"], when=DAY_ONE)
    _seed(db, other["merchant"]["id"], wallet.id, ["700.00"], when=DAY_ONE)

    response = client.get(
        f"/merchants/{mine['merchant']['id']}/transactions?date=2025-03-01",
        headers=auth_header(mine),
    )

    assert response.json()["total_amount"] == "300.00"
    assert response.json()["count"] == 1


def test_list_requires_authentication(client, db) -> None:
    registration = register_merchant(client, email="list-anon@example.com")
    response = client.get(f"/merchants/{registration['merchant']['id']}/transactions")
    assert response.status_code == 401


def test_list_rejects_another_merchants_account(client, db) -> None:
    mine = register_merchant(client, email="list-owner@example.com")
    other = register_merchant(client, email="list-stranger@example.com")
    response = client.get(
        f"/merchants/{mine['merchant']['id']}/transactions",
        headers=auth_header(other),
    )
    assert response.status_code == 403


def test_empty_day_is_reported_as_zero(client, db) -> None:
    registration = register_merchant(client, email="list-empty@example.com")
    response = client.get(
        f"/merchants/{registration['merchant']['id']}/transactions?date=2020-01-01",
        headers=auth_header(registration),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 0
    assert body["total_amount"] == "0.00"
    assert body["transactions"] == []


def test_limit_caps_rows_but_not_the_total(client, db) -> None:
    registration = register_merchant(client, email="list-limit@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    for index in range(5):
        make_transaction(
            db,
            merchant_id=merchant_id,
            wallet_id=wallet.id,
            amount="10.00",
            created_at=DAY_ONE,
            idempotency_key=f"cap-{index}",
        )

    response = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-01&limit=2",
        headers=auth_header(registration),
    )

    body = response.json()
    assert len(body["transactions"]) == 2
    assert body["count"] == 5
    assert body["total_amount"] == "50.00"


def test_midnight_transaction_lands_on_the_later_day(client, db) -> None:
    """Must agree with settlement's day bucketing, or the two screens disagree."""
    registration = register_merchant(client, email="list-midnight@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db,
        merchant_id=merchant_id,
        wallet_id=wallet.id,
        amount="60.00",
        created_at=datetime(2025, 3, 2, 0, 0, 0, tzinfo=UTC),
        idempotency_key="midnight",
    )

    first = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-01",
        headers=auth_header(registration),
    ).json()
    second = client.get(
        f"/merchants/{merchant_id}/transactions?date=2025-03-02",
        headers=auth_header(registration),
    ).json()

    assert Decimal(first["total_amount"]) == Decimal("0.00")
    assert Decimal(second["total_amount"]) == Decimal("60.00")
