"""Nightly settlement tasks.

These are thin wrappers: the arithmetic lives in ``app.services.settlements``
so the scheduled job and the manual demo endpoint cannot drift apart.
"""

from datetime import date

from sqlalchemy import select

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import Merchant
from app.services.settlements import (
    UnknownMerchantError,
    settle_merchant_for_date,
    yesterday,
)


def _parse_date(value: str | None) -> date:
    if value is None:
        return yesterday()
    return date.fromisoformat(value)


@celery_app.task(name="settlement.settle_merchant")
def settle_merchant(merchant_id: int, batch_date: str | None = None) -> dict:
    """Settle a single merchant for ``batch_date`` (defaults to yesterday)."""
    target = _parse_date(batch_date)
    session = SessionLocal()
    try:
        result = settle_merchant_for_date(session, merchant_id=merchant_id, target=target)
    except UnknownMerchantError as exc:
        return {
            "merchant_id": merchant_id,
            "batch_date": target.isoformat(),
            "status": "unknown_merchant",
            "detail": str(exc),
        }
    finally:
        session.close()

    return {
        "merchant_id": result.merchant_id,
        "batch_date": result.batch_date.isoformat(),
        "status": "settled" if result.created else "nothing_to_settle",
        "batch_id": result.batch.id if result.batch else None,
        "transaction_count": result.transaction_count,
        "total_amount": str(result.total_amount),
        "commission_rate": str(result.commission_rate),
        "commission_deducted": str(result.commission_deducted),
        "net_payout": str(result.net_payout),
    }


@celery_app.task(name="settlement.settle_all_merchants")
def settle_all_merchants(batch_date: str | None = None) -> dict:
    """The nightly job: settle every merchant for the same day.

    One merchant failing must not abort the run for the others, so errors are
    collected per merchant and reported back in the result.
    """
    target = _parse_date(batch_date)
    session = SessionLocal()
    try:
        merchant_ids = list(session.scalars(select(Merchant.id).order_by(Merchant.id)).all())
    finally:
        session.close()

    settled: list[dict] = []
    nothing_to_settle: list[int] = []
    errors: list[dict] = []

    for merchant_id in merchant_ids:
        outcome = settle_merchant.apply(args=(merchant_id, target.isoformat())).get()
        if outcome["status"] == "settled":
            settled.append(outcome)
        elif outcome["status"] == "nothing_to_settle":
            nothing_to_settle.append(merchant_id)
        else:
            errors.append(outcome)

    return {
        "batch_date": target.isoformat(),
        "merchants_examined": len(merchant_ids),
        "batches_created": len(settled),
        "nothing_to_settle": nothing_to_settle,
        "settled": settled,
        "errors": errors,
    }
