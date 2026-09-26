from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class OrphanedPaymentOut(BaseModel):
    """A completed payment that no settlement batch covered."""

    transaction_id: int
    wallet_id: int
    amount: Decimal
    created_at: datetime


class AmountMismatchOut(BaseModel):
    """A batch whose items do not add up to its recorded total."""

    batch_id: int
    batch_date: date
    recorded_total: Decimal
    item_sum: Decimal
    difference: Decimal


class CommissionMismatchOut(BaseModel):
    """A batch whose net payout is not its total minus its commission."""

    batch_id: int
    batch_date: date
    total_amount: Decimal
    commission_deducted: Decimal
    net_payout: Decimal
    expected_net_payout: Decimal
    difference: Decimal


class ReconciliationSummaryOut(BaseModel):
    transaction_count: int
    settled_transaction_count: int
    batch_count: int
    total_settled_gross: Decimal
    total_net_paid: Decimal
    total_orphaned_amount: Decimal
    orphaned_payment_count: int
    amount_mismatch_count: int
    commission_mismatch_count: int
    is_balanced: bool = Field(description="True when all three mismatch categories are empty.")


class ReconciliationReportOut(BaseModel):
    merchant_id: int
    generated_at: datetime
    detail_truncated: bool = Field(
        description=(
            "True when a mismatch category had more entries than the requested "
            "detail limit. The summary counts are always complete."
        )
    )
    summary: ReconciliationSummaryOut
    orphaned_payments: list[OrphanedPaymentOut]
    amount_mismatches: list[AmountMismatchOut]
    commission_mismatches: list[CommissionMismatchOut]
