"""Reconciliation report.

Each mismatch category is proven by seeding a *deliberately broken* book and
asserting the report names it. A test that only ever reconciles healthy data
would pass even if the checks did nothing.
"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.factories import (
    auth_header,
    make_merchant,
    make_transaction,
    make_wallet,
    register_merchant,
)

from app.models import (
    SettlementBatch,
    SettlementItem,
    TransactionStatus,
)
from app.services.reconciliation import build_report
from app.services.settlements import settle_merchant_for_date

DAY_ONE = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
DAY_TWO = datetime(2025, 3, 2, 10, 0, tzinfo=UTC)


def _corrupt_total(db, batch: SettlementBatch, recorded_total: str) -> None:
    """Rewrite a batch total behind the service's back, as a bad migration would."""
    batch.total_amount = Decimal(recorded_total)
    db.flush()


def _corrupt_net_payout(db, batch: SettlementBatch, net_payout: str) -> None:
    batch.net_payout = Decimal(net_payout)
    db.flush()


# --------------------------------------------------------------------------
# Healthy books
# --------------------------------------------------------------------------


def test_a_freshly_settled_book_reconciles_clean(db) -> None:
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    for amount in ("100.00", "250.00", "75.50"):
        make_transaction(
            db, merchant_id=merchant.id, wallet_id=wallet.id, amount=amount, created_at=DAY_ONE
        )
    settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is True
    assert report.summary.orphaned_payment_count == 0
    assert report.summary.amount_mismatch_count == 0
    assert report.summary.commission_mismatch_count == 0
    assert report.summary.transaction_count == 3
    assert report.summary.settled_transaction_count == 3
    assert report.summary.batch_count == 1
    assert report.summary.total_settled_gross == Decimal("425.50")
    assert report.orphaned_payments == []


def test_a_merchant_with_no_activity_reconciles_clean(db) -> None:
    merchant = make_merchant(db)
    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is True
    assert report.summary.transaction_count == 0
    assert report.summary.batch_count == 0
    assert report.summary.total_settled_gross == Decimal("0.00")


def test_reconciliation_ignores_another_merchants_data(db) -> None:
    mine = make_merchant(db, email="mine@example.com")
    theirs = make_merchant(db, email="theirs@example.com")
    wallet = make_wallet(db)
    # The other merchant has an orphan; it must not show up in my report.
    make_transaction(
        db, merchant_id=theirs.id, wallet_id=wallet.id, amount="500.00", created_at=DAY_ONE
    )

    report = build_report(db, merchant_id=mine.id)

    assert report.summary.orphaned_payment_count == 0
    assert report.summary.is_balanced is True


# --------------------------------------------------------------------------
# Category 1: orphaned payments
# --------------------------------------------------------------------------


def test_completed_transaction_with_no_batch_is_orphaned(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    settled = make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    # Arrives after settlement ran, and the nightly job has not caught up.
    stray = make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="250.00", created_at=DAY_TWO
    )

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is False
    assert report.summary.orphaned_payment_count == 1
    assert report.summary.total_orphaned_amount == Decimal("250.00")
    assert [p.transaction_id for p in report.orphaned_payments] == [stray.id]
    assert report.orphaned_payments[0].amount == Decimal("250.00")
    # The settled one is correctly not flagged.
    assert settled.id not in [p.transaction_id for p in report.orphaned_payments]


def test_unsettled_but_not_completed_transactions_are_not_orphans(db) -> None:
    """Only completed payments count as orphaned.

    A failed payment is not money the merchant is owed, and flagging it would
    bury the real exceptions in noise.
    """
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    for status in (
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
        TransactionStatus.REVERSED,
    ):
        make_transaction(
            db,
            merchant_id=merchant.id,
            wallet_id=wallet.id,
            amount="500.00",
            status=status,
            created_at=DAY_ONE,
        )

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.orphaned_payment_count == 0
    assert report.summary.is_balanced is True


# --------------------------------------------------------------------------
# Category 2: amount mismatches
# --------------------------------------------------------------------------


def test_batch_whose_items_do_not_sum_to_its_total_is_flagged(db) -> None:
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="200.00", created_at=DAY_ONE
    )
    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    assert result.total_amount == Decimal("300.00")
    assert result.net_payout == Decimal("294.00")

    # The batch claims 350.00 but its items only add up to 300.00. net_payout is
    # moved with it so the batch's own arithmetic still adds up -- otherwise
    # this test would pass on the commission check and prove nothing about the
    # amount check.
    _corrupt_total(db, result.batch, "350.00")
    _corrupt_net_payout(db, result.batch, "344.00")

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is False
    assert report.summary.amount_mismatch_count == 1
    assert report.summary.commission_mismatch_count == 0
    mismatch = report.amount_mismatches[0]
    assert mismatch.batch_id == result.batch.id
    assert mismatch.recorded_total == Decimal("350.00")
    assert mismatch.item_sum == Decimal("300.00")
    assert mismatch.difference == Decimal("50.00")


def test_corrupting_a_total_also_breaks_the_batchs_own_arithmetic(db) -> None:
    """The two categories are independent but can fire together.

    Editing a total in place leaves net_payout stale, so the batch stops
    satisfying net = total - commission and is reported under both headings.
    """
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="300.00", created_at=DAY_ONE
    )
    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    _corrupt_total(db, result.batch, "999.00")

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.amount_mismatch_count == 1
    assert report.summary.commission_mismatch_count == 1
    commission = report.commission_mismatches[0]
    # commission_deducted is still the 6.00 recorded at settlement time, so
    # 999.00 - 6.00 = 993.00 is what a correct batch would have paid out.
    assert commission.expected_net_payout == Decimal("993.00")
    assert commission.net_payout == Decimal("294.00")


def test_a_batch_with_no_items_at_all_is_flagged(db) -> None:
    """Zero items must be compared against the total, not skipped as missing."""
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    db.query(SettlementItem).filter(SettlementItem.batch_id == result.batch.id).delete()
    db.flush()

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.amount_mismatch_count == 1
    assert report.amount_mismatches[0].item_sum == Decimal("0.00")
    assert report.amount_mismatches[0].difference == Decimal("100.00")


# --------------------------------------------------------------------------
# Category 3: commission mismatches
# --------------------------------------------------------------------------


def test_batch_whose_net_payout_disagrees_with_its_own_maths_is_flagged(db) -> None:
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="500.00", created_at=DAY_ONE
    )
    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    assert result.net_payout == Decimal("490.00")

    # 500.00 - 10.00 = 490.00, so 495.00 is internally inconsistent.
    _corrupt_net_payout(db, result.batch, "495.00")

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is False
    assert report.summary.commission_mismatch_count == 1
    mismatch = report.commission_mismatches[0]
    assert mismatch.batch_id == result.batch.id
    assert mismatch.total_amount == Decimal("500.00")
    assert mismatch.commission_deducted == Decimal("10.00")
    assert mismatch.net_payout == Decimal("495.00")
    assert mismatch.expected_net_payout == Decimal("490.00")
    assert mismatch.difference == Decimal("5.00")


def test_changing_a_merchants_rate_does_not_retroactively_flag_batches(db) -> None:
    """A batch written at 2% stays valid after the rate moves to 3%.

    Re-deriving the commission from today's rate would report every historical
    batch as broken, so the check only tests the batch's internal arithmetic.
    """
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="1000.00", created_at=DAY_ONE
    )
    settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    merchant.commission_rate = Decimal("0.03")
    db.flush()

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.commission_mismatch_count == 0
    assert report.summary.is_balanced is True


def test_all_three_categories_are_reported_together(db) -> None:
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)

    # Day one: settle, then corrupt both the total and the net payout.
    for amount in ("100.00", "200.00"):
        make_transaction(
            db, merchant_id=merchant.id, wallet_id=wallet.id, amount=amount, created_at=DAY_ONE
        )
    first = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    _corrupt_total(db, first.batch, "350.00")
    _corrupt_net_payout(db, first.batch, "299.00")

    # Day two: a completed payment that was never settled.
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="77.00", created_at=DAY_TWO
    )

    report = build_report(db, merchant_id=merchant.id)

    assert report.summary.is_balanced is False
    assert report.summary.orphaned_payment_count == 1
    assert report.summary.amount_mismatch_count == 1
    assert report.summary.commission_mismatch_count == 1


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------


def test_detail_lists_are_capped_but_counts_stay_exact(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    for index in range(5):
        make_transaction(
            db,
            merchant_id=merchant.id,
            wallet_id=wallet.id,
            amount="10.00",
            created_at=DAY_ONE,
            idempotency_key=f"orphan-{index}",
        )

    report = build_report(db, merchant_id=merchant.id, limit=2)

    assert report.summary.orphaned_payment_count == 5
    assert report.summary.total_orphaned_amount == Decimal("50.00")
    assert len(report.orphaned_payments) == 2
    assert report.detail_truncated is True


def test_detail_is_not_marked_truncated_when_everything_fits(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="10.00", created_at=DAY_ONE
    )

    report = build_report(db, merchant_id=merchant.id, limit=100)

    assert report.detail_truncated is False


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def test_reconciliation_endpoint_requires_authentication(client, db) -> None:
    registration = register_merchant(client, email="recon-anon@example.com")
    response = client.get(f"/merchants/{registration['merchant']['id']}/reconciliation")
    assert response.status_code == 401


def test_reconciliation_endpoint_rejects_another_merchant(client, db) -> None:
    mine = register_merchant(client, email="recon-mine@example.com")
    other = register_merchant(client, email="recon-other@example.com")

    response = client.get(
        f"/merchants/{mine['merchant']['id']}/reconciliation",
        headers=auth_header(other),
    )
    assert response.status_code == 403


def test_reconciliation_endpoint_reports_each_category(client, db) -> None:
    registration = register_merchant(client, email="recon-endpoint@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    for amount in ("100.00", "200.00"):
        make_transaction(
            db, merchant_id=merchant_id, wallet_id=wallet.id, amount=amount, created_at=DAY_ONE
        )
    result = settle_merchant_for_date(db, merchant_id=merchant_id, target=DAY_ONE.date())
    _corrupt_total(db, result.batch, "999.00")
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="12.00", created_at=DAY_TWO
    )

    response = client.get(
        f"/merchants/{merchant_id}/reconciliation", headers=auth_header(registration)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["merchant_id"] == merchant_id
    assert body["summary"]["is_balanced"] is False
    assert body["summary"]["orphaned_payment_count"] == 1
    assert body["summary"]["amount_mismatch_count"] == 1
    # Overwriting the total left net_payout stale, so the commission check
    # fires as well. See test_corrupting_a_total_also_breaks_the_batchs_own_arithmetic.
    assert body["summary"]["commission_mismatch_count"] == 1
    assert body["amount_mismatches"][0]["difference"] == "699.00"
    assert body["commission_mismatches"][0]["expected_net_payout"] == "993.00"
    assert body["orphaned_payments"][0]["amount"] == "12.00"
    assert body["detail_truncated"] is False


def test_reconciliation_endpoint_validates_the_limit(client, db) -> None:
    registration = register_merchant(client, email="recon-limit@example.com")
    response = client.get(
        f"/merchants/{registration['merchant']['id']}/reconciliation?limit=0",
        headers=auth_header(registration),
    )
    assert response.status_code == 422


def test_reconciliation_endpoint_is_empty_for_a_healthy_merchant(client, db) -> None:
    registration = register_merchant(client, email="recon-clean@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="80.00", created_at=DAY_ONE
    )
    settle_merchant_for_date(db, merchant_id=merchant_id, target=DAY_ONE.date())

    response = client.get(
        f"/merchants/{merchant_id}/reconciliation", headers=auth_header(registration)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["is_balanced"] is True
    assert body["summary"]["total_net_paid"] == "78.40"
    assert body["amount_mismatches"] == []
    assert body["commission_mismatches"] == []
    assert body["orphaned_payments"] == []
