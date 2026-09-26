"""Signed QR / invoice payloads.

A merchant issues a fixed-amount invoice; the encoded string is what a customer
actually scans. It is signed with HMAC-SHA256 so the amount a customer sees
cannot be edited on the way to the payment endpoint, and it carries an expiry
so a screenshot of an old QR stops working.

The token is a compact ``base64url(payload).base64url(signature)`` string, which
is small enough to sit comfortably inside a QR code.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.config import get_settings

DEFAULT_TTL_SECONDS = 15 * 60
MAX_TTL_SECONDS = 24 * 60 * 60


class InvalidInvoiceToken(ValueError):
    """Raised when an invoice token is malformed, tampered with, or expired."""


@dataclass(frozen=True)
class InvoiceClaims:
    invoice_ref: str
    merchant_id: int
    amount: Decimal
    currency: str
    issued_at: int
    expires_at: int

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(payload_b64: str) -> str:
    settings = get_settings()
    digest = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def create_invoice_token(
    *,
    merchant_id: int,
    amount: Decimal,
    currency: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, InvoiceClaims]:
    """Mint a signed invoice. Returns the token and its decoded claims."""
    if amount <= Decimal("0"):
        raise ValueError("invoice amount must be positive")
    ttl_seconds = max(1, min(ttl_seconds, MAX_TTL_SECONDS))

    issued_at = int(time.time())
    claims = InvoiceClaims(
        invoice_ref=uuid.uuid4().hex,
        merchant_id=merchant_id,
        amount=amount,
        currency=currency,
        issued_at=issued_at,
        expires_at=issued_at + ttl_seconds,
    )

    payload = {
        "ref": claims.invoice_ref,
        "mid": claims.merchant_id,
        # Decimal is serialised as a string so no precision is lost to float.
        "amt": format(claims.amount, "f"),
        "cur": claims.currency,
        "iat": claims.issued_at,
        "exp": claims.expires_at,
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload_b64}.{_signature(payload_b64)}", claims


def verify_invoice_token(token: str) -> InvoiceClaims:
    """Validate a signed invoice and return its claims.

    Raises InvalidInvoiceToken if the signature does not match, the token is
    malformed, or the invoice has expired.
    """
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidInvoiceToken("invoice token must be <payload>.<signature>") from exc

    expected = _signature(payload_b64)
    if not hmac.compare_digest(expected, signature):
        raise InvalidInvoiceToken("invoice signature is not valid")

    try:
        payload = json.loads(_b64decode(payload_b64))
        claims = InvoiceClaims(
            invoice_ref=payload["ref"],
            merchant_id=int(payload["mid"]),
            amount=Decimal(payload["amt"]),
            currency=payload["cur"],
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidInvoiceToken("invoice payload is malformed") from exc

    if claims.is_expired:
        raise InvalidInvoiceToken("invoice has expired")
    return claims
