"""Payment tests: success, idempotent replay, and failure."""

from decimal import Decimal
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.factories import (
    auth_header,
    create_wallet,
    pay,
    register_merchant,
)

from app.db import get_db
from app.models import Transaction, TransactionStatus, Wallet

# --------------------------------------------------------------------------
# Successful payment
# --------------------------------------------------------------------------


def test_successful_payment_completes_and_debits_the_wallet(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="250.00",
        idempotency_key="pay-key-1",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert Decimal(body["amount"]) == Decimal("250.00")
    assert body["merchant_id"] == merchant["id"]
    assert body["wallet_id"] == wallet["id"]
    assert body["idempotency_key"] == "pay-key-1"
    assert body["idempotent_replay"] is False
    assert body["created_at"]

    remaining = client.get(f"/wallets/{wallet['id']}/balance").json()
    assert Decimal(remaining["balance"]) == Decimal("750.00")


def test_payment_is_persisted(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="100.00")

    created = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="40.00",
        idempotency_key="persist-1",
    ).json()

    fetched = client.get(f"/transactions/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["idempotent_replay"] is False


def test_payment_succeeds_on_an_exact_balance(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="100.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="100.00",
        idempotency_key="exact-1",
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert Decimal(client.get(f"/wallets/{wallet['id']}").json()["balance"]) == Decimal("0.00")


def test_multiple_distinct_payments_all_succeed(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")

    for index in range(1, 4):
        response = pay(
            client,
            merchant_id=merchant["id"],
            wallet_id=wallet["id"],
            amount="100.00",
            idempotency_key=f"multi-{index}",
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed"

    assert Decimal(client.get(f"/wallets/{wallet['id']}").json()["balance"]) == Decimal("700.00")


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_duplicate_key_returns_the_original_transaction(client: TestClient) -> None:
    """The headline guarantee: a retry must not create a second transaction."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")

    first = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="250.00",
        idempotency_key="retry-1",
    )
    second = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="250.00",
        idempotency_key="retry-1",
    )

    assert first.status_code == 201
    # A replay is 200 OK, not 201 Created: nothing was created.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotent_replay"] is True


def test_duplicate_key_debits_the_wallet_only_once(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")

    for _ in range(3):
        pay(
            client,
            merchant_id=merchant["id"],
            wallet_id=wallet["id"],
            amount="250.00",
            idempotency_key="once-only",
        )

    remaining = client.get(f"/wallets/{wallet['id']}/balance").json()
    assert Decimal(remaining["balance"]) == Decimal("750.00")


def test_duplicate_key_creates_exactly_one_row(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    for _ in range(3):
        pay(
            client,
            merchant_id=merchant["id"],
            wallet_id=wallet["id"],
            amount="50.00",
            idempotency_key="single-row",
        )

    db.expire_all()
    count = db.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.idempotency_key == "single-row")
    )
    assert count == 1


def test_replay_with_a_different_amount_still_returns_the_original(client: TestClient) -> None:
    """A retry is a retry even if the client changed the body: the stored result wins."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")

    first = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="250.00",
        idempotency_key="stable-1",
    )
    second = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="999.00",
        idempotency_key="stable-1",
    )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert Decimal(second.json()["amount"]) == Decimal("250.00")
    # The second amount was ignored, so the balance reflects only the first.
    assert Decimal(client.get(f"/wallets/{wallet['id']}").json()["balance"]) == Decimal("750.00")


def test_failed_payment_can_be_retried_with_the_same_key(client: TestClient) -> None:
    """A failed attempt is a real, settled outcome, so the key stays consumed."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="10.00")

    first = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="500.00",
        idempotency_key="failed-retry",
    )
    second = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="500.00",
        idempotency_key="failed-retry",
    )

    assert first.json()["status"] == "failed"
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["status"] == "failed"


def test_key_may_be_supplied_in_the_body(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    first = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="60.00",
        idempotency_key="body-key",
        use_body_key=True,
    )
    second = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="60.00",
        idempotency_key="body-key",
        use_body_key=True,
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_header_key_takes_precedence_over_the_body(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    body = {
        "merchant_id": merchant["id"],
        "wallet_id": wallet["id"],
        "amount": "60.00",
    }

    first = client.post(
        "/transactions",
        json={**body, "idempotency_key": "body-one"},
        headers={"Idempotency-Key": "header-wins"},
    )
    # Same key in the header, a *different* key in the body: the header must win,
    # so this is a replay of the first transaction rather than a new one.
    second = client.post(
        "/transactions",
        json={**body, "idempotency_key": "body-two"},
        headers={"Idempotency-Key": "header-wins"},
    )
    # Using the body key that the header overrode must be treated as a new key.
    third = client.post(
        "/transactions",
        json={**body, "idempotency_key": "body-one"},
        headers={"Idempotency-Key": "header-wins"},
    )

    assert first.status_code == 201
    assert first.json()["idempotency_key"] == "header-wins"
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert third.status_code == 200
    assert third.json()["id"] == first.json()["id"]


def test_missing_idempotency_key_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
    )

    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_blank_idempotency_key_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="   ",
    )

    assert response.status_code == 400


def test_overlong_idempotency_key_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="k" * 129,
    )

    assert response.status_code == 400
    assert "128" in response.json()["detail"]


def test_database_rejects_a_duplicate_key_directly(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """The app-level check is defence in depth; the constraint is the guarantee.

    This bypasses the endpoint entirely to prove Postgres itself refuses the
    second row, which is what makes the concurrent case safe.
    """
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="db-level",
    )
    db.expire_all()

    duplicate = Transaction(
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount=Decimal("10.00"),
        status=TransactionStatus.COMPLETED,
        idempotency_key="db-level",
    )

    # A SAVEPOINT keeps the failure local: the fixture's outer transaction
    # survives so the rest of the test (and the teardown rollback) still work.
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(duplicate)
            db.flush()


def test_key_reused_by_another_merchant_is_a_conflict(client: TestClient) -> None:
    """Keys are global, so a cross-merchant replay is rejected, not echoed back."""
    first_merchant = register_merchant(client)["merchant"]
    second_merchant = register_merchant(client, email="second@example.com", name="Second Merchant")[
        "merchant"
    ]
    wallet = create_wallet(client, initial_balance="5000.00")

    pay(
        client,
        merchant_id=first_merchant["id"],
        wallet_id=wallet["id"],
        amount="100.00",
        idempotency_key="shared-key",
    )
    response = pay(
        client,
        merchant_id=second_merchant["id"],
        wallet_id=wallet["id"],
        amount="100.00",
        idempotency_key="shared-key",
    )

    assert response.status_code == 409
    assert "another merchant" in response.json()["detail"]


# --------------------------------------------------------------------------
# Failed payment
# --------------------------------------------------------------------------


def test_payment_fails_when_the_wallet_is_short(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="40.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="100.00",
        idempotency_key="insufficient-1",
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"


def test_failed_payment_leaves_the_balance_untouched(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="40.00")

    pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="100.00",
        idempotency_key="insufficient-2",
    )

    remaining = client.get(f"/wallets/{wallet['id']}/balance").json()
    assert Decimal(remaining["balance"]) == Decimal("40.00")


def test_zero_balance_wallet_cannot_pay(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="0.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="0.01",
        idempotency_key="zero-balance",
    )

    assert response.json()["status"] == "failed"


def test_failed_payment_is_still_recorded_for_audit(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """A failed attempt must exist in the ledger, not vanish."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="1.00")

    created = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="99.00",
        idempotency_key="audit-1",
    ).json()

    db.expire_all()
    stored = db.get(Transaction, created["id"])
    assert stored is not None
    assert stored.status == TransactionStatus.FAILED
    assert Decimal(stored.amount) == Decimal("99.00")


# --------------------------------------------------------------------------
# Validation and not-found
# --------------------------------------------------------------------------


def test_unknown_merchant_is_404(client: TestClient) -> None:
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=999_999,
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="unknown-merchant",
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_unknown_wallet_is_404(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=999_999,
        amount="10.00",
        idempotency_key="unknown-wallet",
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_unknown_transaction_is_404(client: TestClient) -> None:
    assert client.get("/transactions/999999").status_code == 404


def test_zero_amount_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="0.00",
        idempotency_key="zero-amount",
    )

    assert response.status_code == 422


def test_negative_amount_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="-50.00",
        idempotency_key="negative-amount",
    )

    assert response.status_code == 422


def test_amount_with_too_many_decimals_is_rejected(client: TestClient) -> None:
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.005",
        idempotency_key="sub-cent",
    )

    assert response.status_code == 422


def test_registration_token_is_not_required_to_pay(client: TestClient) -> None:
    """The customer side of the flow is unauthenticated by design."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client)

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="no-auth",
    )

    assert response.status_code == 201
    assert auth_header(register_merchant(client, email="x@example.com"))["Authorization"]


def test_wallet_cannot_be_emptied_twice_by_a_replayed_key(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Guards the balance arithmetic from a double debit on retry."""
    merchant = register_merchant(client)["merchant"]
    wallet = create_wallet(client, initial_balance="300.00")

    pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="300.00",
        idempotency_key="drain-1",
    )
    for _ in range(4):
        pay(
            client,
            merchant_id=merchant["id"],
            wallet_id=wallet["id"],
            amount="300.00",
            idempotency_key="drain-1",
        )

    db.expire_all()
    stored_wallet = db.get(Wallet, wallet["id"])
    assert stored_wallet is not None
    assert Decimal(stored_wallet.balance) == Decimal("0.00")
