"""Merchant invoice / QR payload issuance."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import get_current_merchant
from app.core.invoices import create_invoice_token
from app.db import get_db
from app.models import Merchant
from app.schemas import InvoiceCreate, InvoiceOut

router = APIRouter(prefix="/merchants", tags=["invoices"])


@router.post(
    "/{merchant_id}/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a signed fixed-amount QR invoice",
)
def create_invoice(
    payload: InvoiceCreate,
    merchant_id: Annotated[int, Path(ge=1)],
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
) -> InvoiceOut:
    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only issue invoices for your own merchant account",
        )

    token, claims = create_invoice_token(
        merchant_id=merchant_id,
        amount=payload.amount,
        currency=get_settings().settlement_currency,
        ttl_seconds=payload.ttl_seconds,
    )

    return InvoiceOut(
        invoice_ref=claims.invoice_ref,
        merchant_id=claims.merchant_id,
        amount=Decimal(claims.amount),
        currency=claims.currency,
        issued_at=claims.issued_at,
        expires_at=claims.expires_at,
        qr_payload=token,
        expires_in=claims.expires_at - claims.issued_at,
    )
