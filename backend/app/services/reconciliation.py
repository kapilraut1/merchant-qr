"""Reconciliation.

Compares what was *paid* against what was *recorded*, and reports the three
ways those two books can disagree:

``orphaned_payments``
    Completed transactions that no settlement batch ever covered. Money came
    in that the merchant was never told about.

``amount_mismatches``
    A batch whose items do not add up to its recorded total.

``commission_mismatches``
    A batch whose net payout is not its total minus its recorded commission.

The commission check is deliberately an *internal* consistency test, not a
re-derivation of ``total * commission_rate``. Rates change over time, and a
batch that was correct when it was written would be flagged as wrong by a
report that re-applied today's rate. Verifying the arithmetic that was frozen
into the batch catches real corruption without rewriting history.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import ZERO, net_payout_for, quantize_money
from app.models import (
    SettlementBatch,
    SettlementItem,
    Transaction,
    TransactionStatus,
)

# Detail lists are capped; the counts in the summary are always exact.
DEFAULT_DETAIL_LIMIT = 100
MAX_DETAIL_LIMIT = 500


@dataclass(frozen=True)
class OrphanedPayment:
    transaction_id: int
    wallet_id: int
    amount: Decimal
    created_at: datetime


@dataclass(frozen=True)
class AmountMismatch:
    batch_id: int
    batch_date: date
    recorded_total: Decimal
    item_sum: Decimal

    @property
    def difference(self) -> Decimal:
        return quantize_money(self.recorded_total - self.item_sum)


@dataclass(frozen=True)
class CommissionMismatch:
    batch_id: int
    batch_date: date
    total_amount: Decimal
    commission_deducted: Decimal
    net_payout: Decimal
    expected_net_payout: Decimal

    @property
    def difference(self) -> Decimal:
        return quantize_money(self.net_payout - self.expected_net_payout)


@dataclass(frozen=True)
class ReconciliationSummary:
    transaction_count: int
    settled_transaction_count: int
    batch_count: int
    total_settled_gross: Decimal
    total_net_paid: Decimal
    total_orphaned_amount: Decimal
    orphaned_payment_count: int
    amount_mismatch_count: int
    commission_mismatch_count: int

    @property
    def is_balanced(self) -> bool:
        return (
            self.orphaned_payment_count == 0
            and self.amount_mismatch_count == 0
            and self.commission_mismatch_count == 0
        )


@dataclass(frozen=True)
class ReconciliationReport:
    merchant_id: int
    generated_at: datetime
    detail_truncated: bool
    summary: ReconciliationSummary
    orphaned_payments: list[OrphanedPayment]
    amount_mismatches: list[AmountMismatch]
    commission_mismatches: list[CommissionMismatch]


def orphaned_payments(
    db: Session,
    *,
    merchant_id: int,
    limit: int = DEFAULT_DETAIL_LIMIT,
) -> list[OrphanedPayment]:
    """Completed transactions with no settlement item."""
    stmt = (
        select(Transaction)
        .outerjoin(SettlementItem, SettlementItem.transaction_id == Transaction.id)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
            SettlementItem.id.is_(None),
        )
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(limit)
    )
    return [
        OrphanedPayment(
            transaction_id=txn.id,
            wallet_id=txn.wallet_id,
            amount=txn.amount,
            created_at=txn.created_at,
        )
        for txn in db.scalars(stmt).all()
    ]


def build_report(
    db: Session,
    *,
    merchant_id: int,
    limit: int = DEFAULT_DETAIL_LIMIT,
) -> ReconciliationReport:
    """Run all three checks for one merchant."""
    detail_limit = max(1, min(limit, MAX_DETAIL_LIMIT))

    batches = list(
        db.scalars(
            select(SettlementBatch)
            .where(SettlementBatch.merchant_id == merchant_id)
            .order_by(SettlementBatch.batch_date, SettlementBatch.id)
        ).all()
    )

    # One grouped query beats a per-batch lookup, and coalesce gives a batch
    # with no items a sum of zero so it is compared rather than skipped.
    item_sums = dict(
        db.execute(
            select(SettlementItem.batch_id, func.sum(SettlementItem.amount))
            .join(SettlementBatch, SettlementBatch.id == SettlementItem.batch_id)
            .where(SettlementBatch.merchant_id == merchant_id)
            .group_by(SettlementItem.batch_id)
        ).all()
    )

    amount_mismatches: list[AmountMismatch] = []
    commission_mismatches: list[CommissionMismatch] = []
    total_gross = ZERO
    total_net = ZERO

    for batch in batches:
        total_gross += batch.total_amount
        total_net += batch.net_payout

        item_sum = quantize_money(item_sums.get(batch.id) or ZERO)
        if item_sum != batch.total_amount:
            amount_mismatches.append(
                AmountMismatch(
                    batch_id=batch.id,
                    batch_date=batch.batch_date,
                    recorded_total=batch.total_amount,
                    item_sum=item_sum,
                )
            )

        expected_net = net_payout_for(batch.total_amount, batch.commission_deducted)
        if expected_net != batch.net_payout:
            commission_mismatches.append(
                CommissionMismatch(
                    batch_id=batch.id,
                    batch_date=batch.batch_date,
                    total_amount=batch.total_amount,
                    commission_deducted=batch.commission_deducted,
                    net_payout=batch.net_payout,
                    expected_net_payout=expected_net,
                )
            )

    orphans = orphaned_payments(db, merchant_id=merchant_id, limit=detail_limit)

    # The count and the sum must come from the whole set, not the capped page.
    orphaned_count = db.scalar(
        select(func.count())
        .select_from(Transaction)
        .outerjoin(SettlementItem, SettlementItem.transaction_id == Transaction.id)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
            SettlementItem.id.is_(None),
        )
    )
    orphaned_total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .select_from(Transaction)
        .outerjoin(SettlementItem, SettlementItem.transaction_id == Transaction.id)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
            SettlementItem.id.is_(None),
        )
    )

    settled_count = db.scalar(
        select(func.count())
        .select_from(SettlementItem)
        .join(SettlementBatch, SettlementBatch.id == SettlementItem.batch_id)
        .where(SettlementBatch.merchant_id == merchant_id)
    )
    transaction_count = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.merchant_id == merchant_id)
    )

    summary = ReconciliationSummary(
        transaction_count=transaction_count or 0,
        settled_transaction_count=settled_count or 0,
        batch_count=len(batches),
        total_settled_gross=quantize_money(total_gross),
        total_net_paid=quantize_money(total_net),
        total_orphaned_amount=quantize_money(orphaned_total or ZERO),
        orphaned_payment_count=orphaned_count or 0,
        amount_mismatch_count=len(amount_mismatches),
        commission_mismatch_count=len(commission_mismatches),
    )

    return ReconciliationReport(
        merchant_id=merchant_id,
        generated_at=datetime.now(UTC),
        detail_truncated=(
            summary.orphaned_payment_count > len(orphans)
            or len(amount_mismatches) > detail_limit
            or len(commission_mismatches) > detail_limit
        ),
        summary=summary,
        orphaned_payments=orphans[:detail_limit],
        amount_mismatches=amount_mismatches[:detail_limit],
        commission_mismatches=commission_mismatches[:detail_limit],
    )
