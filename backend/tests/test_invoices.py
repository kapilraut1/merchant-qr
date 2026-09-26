"""Invoice / QR payload tests."""

import base64
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from tests.factories import (
    auth_header,
    create_wallet,
    issue_invoice,
    pay,
    register_merchant,
)

from app.core.invoices import (
    DEFAULT_TTL_SECONDS,
    InvalidInvoiceToken,
    create_invoice_token,
    verify_invoice_token,
)

# --------------------------------------------------------------------------
# Token mechanics
# --------------------------------------------------------------------------


def test_token_round_trip_preserves_the_amount_exactly() -> None:
    """Decimal must survive the trip; a float would corrupt money."""
    token, claims = create_invoice_token(merchant_id=7, amount=Decimal("1234.56"), currency="NPR")

    decoded = verify_invoice_token(token)

    assert decoded == claims
    assert decoded.amount == Decimal("1234.56")
    assert decoded.merchant_id == 7
    assert decoded.currency == "NPR"


@pytest.mark.parametrize(
    "amount",
    ["0.01", "0.10", "99999999.99", "1.00", "100.00"],
)
def test_amount_survives_encoding_for_awkward_values(amount: str) -> None:
    token, _ = create_invoice_token(merchant_id=1, amount=Decimal(amount), currency="NPR")

    assert verify_invoice_token(token).amount == Decimal(amount)


def test_tampered_amount_is_rejected() -> None:
    """Re-encoding a larger amount must not produce a usable token."""
    token, _ = create_invoice_token(merchant_id=1, amount=Decimal("100.00"), currency="NPR")
    payload_b64, signature = token.split(".", 1)

    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload["amt"] = "1.00"
    forged_payload = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode()
    )

    with pytest.raises(InvalidInvoiceToken, match="signature"):
        verify_invoice_token(f"{forged_payload}.{signature}")


def test_tampered_signature_is_rejected() -> None:
    token, _ = create_invoice_token(merchant_id=1, amount=Decimal("10.00"), currency="NPR")
    payload_b64, signature = token.split(".", 1)

    with pytest.raises(InvalidInvoiceToken, match="signature"):
        verify_invoice_token(f"{payload_b64}.{'A' * len(signature)}")


def test_malformed_tokens_are_rejected() -> None:
    for bad in ["", "no-dot", "a.b.c", "!!!.???"]:
        with pytest.raises(InvalidInvoiceToken):
            verify_invoice_token(bad)


def test_expired_invoice_is_rejected() -> None:
    # ttl is clamped to a minimum of one second, so build the token then wait.
    import time

    token, claims = create_invoice_token(
        merchant_id=1, amount=Decimal("10.00"), currency="NPR", ttl_seconds=1
    )
    time.sleep(1.1)

    assert claims.is_expired
    with pytest.raises(InvalidInvoiceToken, match="expired"):
        verify_invoice_token(token)


def test_ttl_is_clamped_to_a_sane_maximum() -> None:
    _, claims = create_invoice_token(
        merchant_id=1, amount=Decimal("10.00"), currency="NPR", ttl_seconds=10**9
    )

    assert claims.expires_at - claims.issued_at <= 24 * 60 * 60


def test_default_ttl_is_fifteen_minutes() -> None:
    _, claims = create_invoice_token(merchant_id=1, amount=Decimal("10.00"), currency="NPR")

    assert claims.expires_at - claims.issued_at == DEFAULT_TTL_SECONDS


def test_non_positive_amount_cannot_be_invoiced() -> None:
    with pytest.raises(ValueError):
        create_invoice_token(merchant_id=1, amount=Decimal("0"), currency="NPR")


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def test_merchant_can_issue_an_invoice(client: TestClient) -> None:
    registration = register_merchant(client)

    invoice = issue_invoice(client, registration, amount="450.75")

    assert invoice["merchant_id"] == registration["merchant"]["id"]
    assert Decimal(invoice["amount"]) == Decimal("450.75")
    assert invoice["currency"] == "NPR"
    assert invoice["qr_payload"]
    assert invoice["expires_at"] > invoice["issued_at"]
    assert invoice["expires_in"] == 15 * 60


def test_returned_qr_payload_is_a_valid_invoice(client: TestClient) -> None:
    registration = register_merchant(client)

    invoice = issue_invoice(client, registration, amount="99.99")
    claims = verify_invoice_token(invoice["qr_payload"])

    assert claims.amount == Decimal("99.99")
    assert claims.invoice_ref == invoice["invoice_ref"]
    assert claims.merchant_id == registration["merchant"]["id"]


def test_issuing_an_invoice_requires_authentication(client: TestClient) -> None:
    registration = register_merchant(client)
    merchant_id = registration["merchant"]["id"]

    response = client.post(f"/merchants/{merchant_id}/invoices", json={"amount": "10.00"})

    assert response.status_code == 401


def test_merchant_cannot_issue_for_another_merchant(client: TestClient) -> None:
    registration = register_merchant(client)
    other = register_merchant(client, email="other@example.com", name="Other")["merchant"]

    response = client.post(
        f"/merchants/{other['id']}/invoices",
        json={"amount": "10.00"},
        headers=auth_header(registration),
    )

    assert response.status_code == 403


def test_invoice_amount_must_be_positive(client: TestClient) -> None:
    registration = register_merchant(client)

    response = client.post(
        f"/merchants/{registration['merchant']['id']}/invoices",
        json={"amount": "0"},
        headers=auth_header(registration),
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Paying against an invoice
# --------------------------------------------------------------------------


def test_payment_with_a_valid_invoice_succeeds(client: TestClient) -> None:
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")
    invoice = issue_invoice(client, registration, amount="300.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="300.00",
        idempotency_key="invoice-pay-1",
        invoice_token=invoice["qr_payload"],
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert Decimal(client.get(f"/wallets/{wallet['id']}").json()["balance"]) == Decimal("700.00")


def test_invoice_amount_must_match_the_payment(client: TestClient) -> None:
    """A customer cannot scan a 10.00 QR and pay 1000.00."""
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="5000.00")
    invoice = issue_invoice(client, registration, amount="10.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="1000.00",
        idempotency_key="invoice-mismatch-1",
        invoice_token=invoice["qr_payload"],
    )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_invoice_merchant_must_match_the_payment(client: TestClient) -> None:
    registration = register_merchant(client)
    other = register_merchant(client, email="other@example.com", name="Other")["merchant"]
    wallet = create_wallet(client, initial_balance="5000.00")
    invoice = issue_invoice(client, registration, amount="10.00")

    response = pay(
        client,
        merchant_id=other["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="invoice-merchant-mismatch",
        invoice_token=invoice["qr_payload"],
    )

    assert response.status_code == 400


def test_tampered_invoice_is_rejected_at_payment(client: TestClient) -> None:
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="5000.00")
    invoice = issue_invoice(client, registration, amount="10.00")
    tampered = invoice["qr_payload"][:-4] + "AAAA"

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="invoice-tampered-1",
        invoice_token=tampered,
    )

    assert response.status_code == 400


def test_garbage_invoice_token_is_rejected(client: TestClient) -> None:
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="5000.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="10.00",
        idempotency_key="invoice-garbage-1",
        invoice_token="not-an-invoice",
    )

    assert response.status_code == 400


def test_paying_without_an_invoice_is_allowed(client: TestClient) -> None:
    """The invoice is an integrity aid, not a prerequisite."""
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="500.00")

    response = pay(
        client,
        merchant_id=merchant["id"],
        wallet_id=wallet["id"],
        amount="25.00",
        idempotency_key="no-invoice-1",
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_paying_the_same_invoice_twice_is_idempotent(client: TestClient) -> None:
    registration = register_merchant(client)
    merchant = registration["merchant"]
    wallet = create_wallet(client, initial_balance="1000.00")
    invoice = issue_invoice(client, registration, amount="200.00")

    kwargs = {
        "merchant_id": merchant["id"],
        "wallet_id": wallet["id"],
        "amount": "200.00",
        "idempotency_key": "same-invoice-1",
        "invoice_token": invoice["qr_payload"],
    }
    first = pay(client, **kwargs)
    second = pay(client, **kwargs)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert Decimal(client.get(f"/wallets/{wallet['id']}").json()["balance"]) == Decimal("800.00")
