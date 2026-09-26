"""Settlement batching.

A settlement run takes every completed transaction for one merchant on one day
that has not already been settled, sums them, applies the merchant's commission
rate and writes a ``SettlementBatch`` plus one ``SettlementItem`` per
transaction.

Both the nightly Celery task and the manual demo endpoint call
:func:`settle_merchant_for_date`, so there is exactly one implementation of the
money maths and one definition of "unsettled".
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.money import ZERO, commission_for, net_payout_for, quantize_money
from app.models import (
    Merchant,
    SettlementBatch,
    SettlementItem,
    SettlementStatus,
    Transaction,
    TransactionStatus,
)


class UnknownMerchantError(Exception):
    pass


@dataclass(frozen=True)
class SettlementResult:
    """Outcome of one settlement run.

    ``batch`` is None when there was nothing to settle, which is the normal
    case for a merchant with no activity rather than an error.
    """

    merchant_id: int
    batch_date: date
    batch: SettlementBatch | None
    transaction_count: int
    total_amount: Decimal
    commission_deducted: Decimal
    net_payout: Decimal
    commission_rate: Decimal

    @property
    def created(self) -> bool:
        return self.batch is not None


def _day_bounds(target: date) -> tuple[datetime, datetime]:
    """Half-open [start, end) UTC range covering ``target``.

    Half-open bounds keep the query index-friendly and make midnight itself
    unambiguous: a transaction created exactly at 00:00:00 belongs to the new
    day, not both.
    """
    start = datetime.combine(target, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def unsettled_transactions(
    db: Session,
    *,
    merchant_id: int,
    target: date,
) -> list[Transaction]:
    """Completed transactions for the merchant on ``target`` with no batch yet.

    The ``settlement_items.id IS NULL`` predicate is what makes a repeat run a
    no-op instead of double-paying a merchant.
    """
    start, end = _day_bounds(target)
    stmt = (
        select(Transaction)
        .outerjoin(SettlementItem, SettlementItem.transaction_id == Transaction.id)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.status == TransactionStatus.COMPLETED,
            Transaction.created_at >= start,
            Transaction.created_at < end,
            SettlementItem.id.is_(None),
        )
        .order_by(Transaction.created_at, Transaction.id)
    )
    return list(db.scalars(stmt).all())


def settle_merchant_for_date(
    db: Session,
    *,
    merchant_id: int,
    target: date,
) -> SettlementResult:
    """Settle one merchant for one day. Safe to call repeatedly."""
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise UnknownMerchantError(f"merchant {merchant_id} does not exist")

    rate = merchant.commission_rate

    # Serialise concurrent runs for this merchant so two batches cannot be
    # built from the same transactions.
    db.scalar(select(Merchant).where(Merchant.id == merchant_id).with_for_update())

    transactions = unsettled_transactions(db, merchant_id=merchant_id, target=target)
    if not transactions:
        return SettlementResult(
            merchant_id=merchant_id,
            batch_date=target,
            batch=None,
            transaction_count=0,
            total_amount=ZERO,
            commission_deducted=ZERO,
            net_payout=ZERO,
            commission_rate=rate,
        )

    total = quantize_money(sum((txn.amount for txn in transactions), start=ZERO))
    commission = commission_for(total, rate)
    net = net_payout_for(total, commission)

    batch = SettlementBatch(
        merchant_id=merchant_id,
        batch_date=target,
        total_amount=total,
        commission_deducted=commission,
        net_payout=net,
        status=SettlementStatus.COMPLETED,
    )
    db.add(batch)
    db.flush()

    for txn in transactions:
        db.add(
            SettlementItem(
                batch_id=batch.id,
                transaction_id=txn.id,
                amount=txn.amount,
            )
        )

    # A UNIQUE violation here means a concurrent run claimed one of these
    # transactions first; dropping the whole batch is the safe outcome.
    db.commit()
    db.refresh(batch)
    return SettlementResult(
        merchant_id=merchant_id,
        batch_date=target,
        batch=batch,
        transaction_count=len(transactions),
        total_amount=total,
        commission_deducted=commission,
        net_payout=net,
        commission_rate=rate,
    )


def yesterday(now: datetime | None = None) -> date:
    """The settlement day for a nightly run, in the configured timezone."""
    settings = get_settings()
    tz = ZoneInfo(settings.settlement_timezone)
    moment = (now or datetime.now(UTC)).astimezone(tz)
    return (moment - timedelta(days=1)).date()
