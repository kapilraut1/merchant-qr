from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import SettlementStatus


class SettlementBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant_id: int
    batch_date: date
    total_amount: Decimal
    commission_deducted: Decimal
    net_payout: Decimal
    status: SettlementStatus
    created_at: datetime


class SettlementItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    transaction_id: int
    amount: Decimal


class SettlementBatchDetail(SettlementBatchOut):
    transaction_count: int
    items: list[SettlementItemOut]


class SettlementRunResponse(BaseModel):
    """What one settlement run did, whether it ran inline or as a Celery task."""

    merchant_id: int
    batch_date: date
    batch_id: int | None
    transaction_count: int
    total_amount: Decimal
    commission_rate: Decimal
    commission_deducted: Decimal
    net_payout: Decimal
    dispatched_as_task: bool = False
    task_id: str | None = None


class SettleRequest(BaseModel):
    batch_date: date | None = Field(
        default=None,
        description="Day to settle. Defaults to the most recent day with activity.",
    )
    dispatch_to_worker: bool = Field(
        default=False,
        description="Queue the work to Celery instead of running it inline. The demo "
        "dashboard runs it inline so the result is visible immediately.",
    )
