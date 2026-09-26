"""Customer-facing payment endpoints.

These routes stand in for the wallet app scanning a merchant QR, so they are
unauthenticated: the signed invoice plus the caller's own wallet is the
"authentication" in this simulation.
"""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_merchant
from app.core.money import ZERO, quantize_money
from app.db import get_db
from app.models import Merchant, Transaction, TransactionStatus
from app.schemas import (
    PaymentCreate,
    PaymentResponse,
    TransactionListOut,
    TransactionOut,
)
from app.services.payments import (
    IdempotencyKeyConflictError,
    InvoiceMismatchError,
    PaymentError,
    UnknownMerchantError,
    UnknownWalletError,
    process_payment,
    resolve_invoice,
)
from app.services.settlements import day_bounds

router = APIRouter(tags=["transactions"])

MAX_IDEMPOTENCY_KEY_LENGTH = 128


def _http_error(exc: PaymentError) -> HTTPException:
    if isinstance(exc, UnknownMerchantError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, UnknownWalletError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, IdempotencyKeyConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, InvoiceMismatchError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(  # pragma: no cover - defensive
        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
    )


@router.post(
    "/transactions",
    response_model=PaymentResponse,
    summary="Pay a merchant invoice (idempotent)",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Idempotent replay: the original transaction is returned."},
        201: {"description": "A new transaction was created."},
    },
)
def create_transaction(
    payload: PaymentCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Client-generated key. Replaying it returns the first result.",
        ),
    ] = None,
) -> PaymentResponse:
    key = (idempotency_key or payload.idempotency_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An Idempotency-Key header (or idempotency_key field) is required",
        )
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters",
        )

    try:
        claims = resolve_invoice(
            payload.invoice_token,
            merchant_id=payload.merchant_id,
            amount=payload.amount,
        )
        result = process_payment(
            db,
            merchant_id=payload.merchant_id,
            wallet_id=payload.wallet_id,
            amount=payload.amount,
            idempotency_key=key,
            invoice_claims=claims,
        )
    except PaymentError as exc:
        raise _http_error(exc) from exc

    # A replay did not create anything, so it answers 200 OK; a genuinely new
    # transaction answers 201 Created.
    response.status_code = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED

    return PaymentResponse(
        **TransactionOut.model_validate(result.transaction).model_dump(),
        idempotent_replay=result.replayed,
    )


@router.get("/transactions/{transaction_id}", response_model=PaymentResponse)
def get_transaction(
    transaction_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> PaymentResponse:
    transaction = db.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found",
        )
    return PaymentResponse.model_validate(transaction)


@router.get(
    "/merchants/{merchant_id}/transactions",
    response_model=TransactionListOut,
    summary="A merchant's transactions for one day",
)
def list_merchant_transactions(
    merchant_id: Annotated[int, Path(ge=1)],
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
    on_date: Annotated[
        date | None,
        Query(alias="date", description="Day to report. Defaults to today (UTC)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TransactionListOut:
    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read transactions for your own merchant account",
        )

    target = on_date or datetime.now(UTC).date()
    start, end = day_bounds(target)

    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(limit)
    ).all()

    # Counted over every completed payment in the day, not just the visible
    # page, so "collected" stays correct when the list is truncated.
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
    )
    count = db.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
    )

    return TransactionListOut(
        merchant_id=merchant_id,
        date=target,
        count=count or 0,
        total_amount=quantize_money(total or ZERO),
        transactions=[TransactionOut.model_validate(row) for row in rows],
    )
