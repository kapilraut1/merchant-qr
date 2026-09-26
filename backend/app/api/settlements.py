"""Manual settlement trigger and settlement history.

The manual trigger exists so the demo can be driven from the dashboard without
waiting for the nightly schedule. It calls the same service the Celery task
does, so the two paths cannot disagree.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_merchant
from app.db import get_db
from app.models import (
    Merchant,
    SettlementBatch,
    SettlementItem,
    Transaction,
    TransactionStatus,
)
from app.schemas import (
    SettlementBatchDetail,
    SettlementBatchOut,
    SettlementItemOut,
    SettlementRunResponse,
    SettleRequest,
)
from app.services.settlements import (
    UnknownMerchantError,
    settle_merchant_for_date,
)

router = APIRouter(prefix="/merchants", tags=["settlements"])


def _default_batch_date(db: Session, merchant_id: int) -> date:
    """The most recent day that actually has completed transactions.

    Settling "today" on demand is usually a no-op, so defaulting to today would
    make the demo button look broken. created_at is stored in UTC, which is the
    same frame the service uses for day boundaries.
    """
    latest = db.scalar(
        select(func.max(Transaction.created_at)).where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
        )
    )
    if latest is None:
        return date.today()
    return latest.date()


@router.post(
    "/{merchant_id}/settle",
    response_model=SettlementRunResponse,
    summary="Run settlement for a merchant now (demo trigger)",
)
def settle_merchant_now(
    merchant_id: int,
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
    payload: SettleRequest | None = None,
) -> SettlementRunResponse:
    request = payload or SettleRequest()

    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only trigger settlement for your own merchant account",
        )

    target = request.batch_date or _default_batch_date(db, merchant_id)

    if request.dispatch_to_worker:
        from app.tasks.settlement import settle_merchant

        async_result = settle_merchant.delay(merchant_id, target.isoformat())
        return SettlementRunResponse(
            merchant_id=merchant_id,
            batch_date=target,
            batch_id=None,
            transaction_count=0,
            total_amount=Decimal("0.00"),
            commission_rate=Decimal("0.00"),
            commission_deducted=Decimal("0.00"),
            net_payout=Decimal("0.00"),
            dispatched_as_task=True,
            task_id=async_result.id,
        )

    try:
        result = settle_merchant_for_date(db, merchant_id=merchant_id, target=target)
    except UnknownMerchantError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return SettlementRunResponse(
        merchant_id=result.merchant_id,
        batch_date=result.batch_date,
        batch_id=result.batch.id if result.batch else None,
        transaction_count=result.transaction_count,
        total_amount=result.total_amount,
        commission_rate=result.commission_rate,
        commission_deducted=result.commission_deducted,
        net_payout=result.net_payout,
    )


@router.get(
    "/{merchant_id}/settlements",
    response_model=list[SettlementBatchOut],
    summary="Settlement history, newest first",
)
def list_settlements(
    merchant_id: int,
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SettlementBatchOut]:
    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read settlement history for your own merchant account",
        )

    batches = db.scalars(
        select(SettlementBatch)
        .where(SettlementBatch.merchant_id == merchant_id)
        .order_by(SettlementBatch.batch_date.desc(), SettlementBatch.id.desc())
        .limit(limit)
    ).all()
    return [SettlementBatchOut.model_validate(batch) for batch in batches]


@router.get(
    "/{merchant_id}/settlements/{batch_id}",
    response_model=SettlementBatchDetail,
    summary="One settlement batch with its items",
)
def get_settlement_batch(
    merchant_id: int,
    batch_id: int,
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
) -> SettlementBatchDetail:
    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read settlement history for your own merchant account",
        )

    batch = db.scalar(
        select(SettlementBatch).where(
            SettlementBatch.id == batch_id,
            SettlementBatch.merchant_id == merchant_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Settlement batch {batch_id} not found",
        )

    items = db.scalars(
        select(SettlementItem)
        .where(SettlementItem.batch_id == batch.id)
        .order_by(SettlementItem.id)
    ).all()

    return SettlementBatchDetail(
        **SettlementBatchOut.model_validate(batch).model_dump(),
        transaction_count=len(items),
        items=[SettlementItemOut.model_validate(item) for item in items],
    )
