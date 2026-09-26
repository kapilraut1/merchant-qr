from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import TransactionStatus


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant_id: int
    wallet_id: int
    amount: Decimal
    status: TransactionStatus
    idempotency_key: str
    created_at: datetime


class PaymentCreate(BaseModel):
    merchant_id: int = Field(gt=0)
    wallet_id: int = Field(gt=0)
    amount: Decimal = Field(gt=Decimal("0"), decimal_places=2)
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Alternative to the Idempotency-Key header. The header wins if both "
        "are supplied.",
    )
    invoice_token: str | None = Field(
        default=None,
        description="Signed invoice returned by the invoice endpoint. Optional, but when "
        "supplied its merchant and amount must match this payment.",
    )


class PaymentResponse(TransactionOut):
    """The created transaction, plus whether this call was an idempotent replay."""

    idempotent_replay: bool = False


class InvoiceCreate(BaseModel):
    amount: Decimal = Field(gt=Decimal("0"), decimal_places=2)
    ttl_seconds: int = Field(default=15 * 60, ge=30, le=24 * 60 * 60)


class InvoiceOut(BaseModel):
    """The payload a customer scans. No QR image is rendered server-side."""

    invoice_ref: str
    merchant_id: int
    amount: Decimal
    currency: str
    issued_at: int
    expires_at: int
    qr_payload: str
    expires_in: int
