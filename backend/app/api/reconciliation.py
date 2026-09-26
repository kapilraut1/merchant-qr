"""Reconciliation report endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_merchant
from app.db import get_db
from app.models import Merchant
from app.schemas.reconciliation import ReconciliationReportOut
from app.services.reconciliation import DEFAULT_DETAIL_LIMIT, build_report

router = APIRouter(prefix="/merchants", tags=["reconciliation"])


@router.get(
    "/{merchant_id}/reconciliation",
    response_model=ReconciliationReportOut,
    summary="Compare settled batches against completed transactions",
)
def reconcile_merchant(
    merchant_id: int,
    current: Annotated[Merchant, Depends(get_current_merchant)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=500,
            description="Maximum mismatch details to list per category.",
        ),
    ] = DEFAULT_DETAIL_LIMIT,
) -> ReconciliationReportOut:
    if current.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only reconcile your own merchant account",
        )

    report = build_report(db, merchant_id=merchant_id, limit=limit)
    return ReconciliationReportOut.model_validate(report, from_attributes=True)
