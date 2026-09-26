"""Test helpers.

Keeping request/row construction in one place means an individual test reads
as a statement about behaviour rather than a pile of setup.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Merchant, Transaction, TransactionStatus, Wallet

DEFAULT_PASSWORD = "sup3r-secret-pw"


def register_merchant(client: TestClient, **overrides: Any) -> dict:
    """Register a merchant over HTTP and return the JSON body."""
    payload = {
        "name": "Everest Coffee House",
        "email": "ops@everestcoffee.example",
        "password": DEFAULT_PASSWORD,
        **overrides,
    }
    response = client.post("/merchants/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(registration: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {registration['access_token']}"}


def create_wallet(client: TestClient, **overrides: Any) -> dict:
    payload = {"customer_id": "cust-001", "initial_balance": "1000.00", **overrides}
    response = client.post("/wallets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def issue_invoice(
    client: TestClient,
    registration: dict,
    amount: str = "250.00",
    **overrides: Any,
) -> dict:
    payload = {"amount": amount, **overrides}
    response = client.post(
        f"/merchants/{registration['merchant']['id']}/invoices",
        json=payload,
        headers=auth_header(registration),
    )
    assert response.status_code == 201, response.text
    return response.json()


def pay(
    client: TestClient,
    *,
    merchant_id: int,
    wallet_id: int,
    amount: str,
    idempotency_key: str | None = None,
    invoice_token: str | None = None,
    use_body_key: bool = False,
) -> Any:
    """Submit a payment, defaulting to the Idempotency-Key header."""
    body: dict[str, Any] = {
        "merchant_id": merchant_id,
        "wallet_id": wallet_id,
        "amount": amount,
    }
    if invoice_token is not None:
        body["invoice_token"] = invoice_token
    headers: dict[str, str] = {}
    if idempotency_key is not None:
        if use_body_key:
            body["idempotency_key"] = idempotency_key
        else:
            headers["Idempotency-Key"] = idempotency_key
    return client.post("/transactions", json=body, headers=headers)


def make_merchant(db: Session, **overrides: Any) -> Merchant:
    values: dict[str, Any] = {
        "name": "Direct Merchant",
        "email": "direct@example.com",
        "password_hash": hash_password(DEFAULT_PASSWORD),
        "commission_rate": Decimal("0.02"),
    }
    values.update(overrides)
    merchant = Merchant(**values)
    db.add(merchant)
    db.flush()
    return merchant


def make_wallet(db: Session, balance: str = "1000.00", **overrides: Any) -> Wallet:
    values: dict[str, Any] = {
        "customer_id": "cust-direct",
        "balance": Decimal(balance),
    }
    values.update(overrides)
    wallet = Wallet(**values)
    db.add(wallet)
    db.flush()
    return wallet


def make_transaction(
    db: Session,
    *,
    merchant_id: int,
    wallet_id: int,
    amount: str = "100.00",
    status: TransactionStatus = TransactionStatus.COMPLETED,
    idempotency_key: str | None = None,
) -> Transaction:
    """Insert a transaction directly, bypassing the payment endpoint."""
    transaction = Transaction(
        merchant_id=merchant_id,
        wallet_id=wallet_id,
        amount=Decimal(amount),
        status=status,
        idempotency_key=idempotency_key or f"seed-{id(db)}-{merchant_id}-{amount}",
    )
    db.add(transaction)
    db.flush()
    return transaction
