"""Settlement batching and commission math.

The pure-arithmetic tests run without a database because the money rules are
the part most worth pinning down precisely: a rounding convention that shifts
by half a paisa is invisible in a smoke test and expensive in production.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.factories import (
    auth_header,
    make_merchant,
    make_transaction,
    make_wallet,
    register_merchant,
)

from app.core.money import commission_for, net_payout_for, quantize_money
from app.models import (
    SettlementBatch,
    SettlementItem,
    TransactionStatus,
)
from app.services.settlements import (
    UnknownMerchantError,
    settle_merchant_for_date,
)

DAY_ONE = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
DAY_TWO = datetime(2025, 3, 2, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# Commission math (no database)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "rate", "expected"),
    [
        ("250.00", "0.025", "6.25"),
        ("1000.00", "0.02", "20.00"),
        ("0.00", "0.025", "0.00"),
        ("33.33", "0.015", "0.50"),
        ("99.99", "0.01", "1.00"),
    ],
)
def test_commission_is_total_times_rate(total: str, rate: str, expected: str) -> None:
    assert commission_for(Decimal(total), Decimal(rate)) == Decimal(expected)


@pytest.mark.parametrize(
    ("total", "commission", "expected"),
    [
        ("250.00", "6.25", "243.75"),
        ("1000.00", "20.00", "980.00"),
        ("7.00", "0.00", "7.00"),
    ],
)
def test_net_payout_is_total_minus_commission(total: str, commission: str, expected: str) -> None:
    assert net_payout_for(Decimal(total), Decimal(commission)) == Decimal(expected)


def test_quantize_rounds_half_away_from_zero_not_to_even() -> None:
    """0.005 must become 0.01.

    Python's default Decimal rounding is ROUND_HALF_EVEN, which would send this
    to 0.00 and silently disagree with the merchant's statement.
    """
    assert quantize_money(Decimal("0.005")) == Decimal("0.01")
    assert quantize_money(Decimal("0.015")) == Decimal("0.02")
    assert quantize_money(Decimal("0.025")) == Decimal("0.03")


def test_net_payout_plus_commission_always_reconciles_to_total() -> None:
    """The invariant the reconciliation report depends on."""
    for total in ("0.01", "1.00", "19.99", "250.00", "12345.67"):
        for rate in ("0.0", "0.01", "0.025", "0.0375"):
            gross = quantize_money(Decimal(total))
            commission = commission_for(gross, Decimal(rate))
            net = net_payout_for(gross, commission)
            assert quantize_money(net + commission) == gross


# --------------------------------------------------------------------------
# Settlement service
# --------------------------------------------------------------------------


def test_settlement_batches_every_completed_transaction_for_the_day(db) -> None:
    merchant = make_merchant(db, commission_rate=Decimal("0.02"))
    wallet = make_wallet(db)
    for amount in ("100.00", "250.50", "49.50"):
        make_transaction(
            db,
            merchant_id=merchant.id,
            wallet_id=wallet.id,
            amount=amount,
            created_at=DAY_ONE,
        )

    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert result.created is True
    assert result.transaction_count == 3
    # 400.00 gross, 2% commission, 392.00 net.
    assert result.total_amount == Decimal("400.00")
    assert result.commission_deducted == Decimal("8.00")
    assert result.net_payout == Decimal("392.00")

    items = db.scalars(
        select(SettlementItem).where(SettlementItem.batch_id == result.batch.id)
    ).all()
    assert len(items) == 3
    assert sum(item.amount for item in items) == result.total_amount


def test_settlement_ignores_non_completed_transactions(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
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

    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert result.transaction_count == 1
    assert result.total_amount == Decimal("100.00")


def test_settlement_only_touches_the_requested_day(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="900.00", created_at=DAY_TWO
    )

    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert result.total_amount == Decimal("100.00")
    # Day two is untouched and still settleable.
    later = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_TWO.date())
    assert later.total_amount == Decimal("900.00")


def test_midnight_belongs_to_a_single_day(db) -> None:
    """A payment stamped exactly 00:00:00 must not be counted in two batches."""
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db,
        merchant_id=merchant.id,
        wallet_id=wallet.id,
        amount="75.00",
        created_at=datetime(2025, 3, 2, 0, 0, 0, tzinfo=UTC),
    )

    first = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    second = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_TWO.date())

    assert first.transaction_count == 0
    assert second.transaction_count == 1


def test_repeat_settlement_creates_no_second_batch(db) -> None:
    """Settling twice must not pay the merchant twice."""
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="300.00", created_at=DAY_ONE
    )

    first = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())
    second = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert first.created is True
    assert second.created is False
    assert second.transaction_count == 0
    assert second.total_amount == Decimal("0.00")
    assert len(db.scalars(select(SettlementBatch)).all()) == 1


def test_late_arrival_lands_in_a_second_batch(db) -> None:
    merchant = make_merchant(db)
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    make_transaction(
        db, merchant_id=merchant.id, wallet_id=wallet.id, amount="50.00", created_at=DAY_ONE
    )
    second = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert second.created is True
    assert second.total_amount == Decimal("50.00")
    assert len(db.scalars(select(SettlementBatch)).all()) == 2


def test_settlement_with_no_activity_creates_nothing(db) -> None:
    merchant = make_merchant(db)
    result = settle_merchant_for_date(db, merchant_id=merchant.id, target=DAY_ONE.date())

    assert result.created is False
    assert result.batch is None
    assert result.net_payout == Decimal("0.00")
    assert db.scalars(select(SettlementBatch)).all() == []


def test_settlement_is_scoped_to_one_merchant(db) -> None:
    mine = make_merchant(db, email="mine@example.com")
    theirs = make_merchant(db, email="theirs@example.com")
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=mine.id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    make_transaction(
        db, merchant_id=theirs.id, wallet_id=wallet.id, amount="700.00", created_at=DAY_ONE
    )

    result = settle_merchant_for_date(db, merchant_id=mine.id, target=DAY_ONE.date())

    assert result.total_amount == Decimal("100.00")


def test_unknown_merchant_raises(db) -> None:
    with pytest.raises(UnknownMerchantError):
        settle_merchant_for_date(db, merchant_id=999_999, target=DAY_ONE.date())


# --------------------------------------------------------------------------
# Manual trigger endpoint
# --------------------------------------------------------------------------


def _settle_day(client, registration, batch_date, **body):
    merchant_id = registration["merchant"]["id"]
    return client.post(
        f"/merchants/{merchant_id}/settle",
        json={"batch_date": batch_date, **body},
        headers=auth_header(registration),
    )


def test_settle_endpoint_requires_authentication(client, db) -> None:
    registration = register_merchant(client, email="auth-required@example.com")
    merchant_id = registration["merchant"]["id"]
    response = client.post(f"/merchants/{merchant_id}/settle", json={})
    assert response.status_code == 401


def test_settle_endpoint_rejects_another_merchants_account(client, db) -> None:
    """A valid token must not authorise settlement for someone else's account."""
    mine = register_merchant(client, email="owner@example.com")
    mine_id = mine["merchant"]["id"]
    make_transaction(
        db,
        merchant_id=mine_id,
        wallet_id=make_wallet(db).id,
        amount="100.00",
        created_at=DAY_ONE,
    )
    other = register_merchant(client, email="intruder@example.com")

    # Path names the victim, token belongs to the attacker.
    response = client.post(
        f"/merchants/{mine_id}/settle",
        json={"batch_date": DAY_ONE.date().isoformat()},
        headers=auth_header(other),
    )
    assert response.status_code == 403


def test_settle_endpoint_creates_a_batch(client, db) -> None:
    registration = register_merchant(client, email="trigger@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="200.00", created_at=DAY_ONE
    )

    response = _settle_day(client, registration, DAY_ONE.date().isoformat())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_id"] is not None
    assert body["transaction_count"] == 1
    assert body["total_amount"] == "200.00"
    assert body["commission_deducted"] == "4.00"
    assert body["net_payout"] == "196.00"
    assert body["dispatched_as_task"] is False


def test_settle_endpoint_defaults_to_the_latest_active_day(client, db) -> None:
    """No batch_date given: settle the most recent day that had activity."""
    registration = register_merchant(client, email="default-day@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="120.00", created_at=DAY_TWO
    )

    response = client.post(
        f"/merchants/{merchant_id}/settle",
        json={},
        headers=auth_header(registration),
    )

    assert response.status_code == 200, response.text
    assert response.json()["batch_date"] == DAY_TWO.date().isoformat()
    assert response.json()["total_amount"] == "120.00"


def test_settle_endpoint_accepts_a_request_with_no_body(client, db) -> None:
    """The dashboard can fire the trigger without building a payload."""
    registration = register_merchant(client, email="no-body@example.com")
    merchant_id = registration["merchant"]["id"]
    response = client.post(f"/merchants/{merchant_id}/settle", headers=auth_header(registration))
    assert response.status_code == 200, response.text
    body = response.json()
    # No transactions seeded, so there is nothing to settle.
    assert body["batch_id"] is None
    assert body["transaction_count"] == 0


def test_settle_endpoint_reports_when_there_is_nothing_to_settle(client, db) -> None:
    registration = register_merchant(client, email="empty@example.com")
    response = _settle_day(client, registration, DAY_ONE.date().isoformat())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_id"] is None
    assert body["transaction_count"] == 0
    assert body["net_payout"] == "0.00"


# --------------------------------------------------------------------------
# Settlement history
# --------------------------------------------------------------------------


def test_settlement_history_is_newest_first(client, db) -> None:
    registration = register_merchant(client, email="history@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="100.00", created_at=DAY_ONE
    )
    make_transaction(
        db, merchant_id=merchant_id, wallet_id=wallet.id, amount="200.00", created_at=DAY_TWO
    )
    _settle_day(client, registration, DAY_ONE.date().isoformat())
    _settle_day(client, registration, DAY_TWO.date().isoformat())

    response = client.get(
        f"/merchants/{merchant_id}/settlements", headers=auth_header(registration)
    )

    assert response.status_code == 200, response.text
    batches = response.json()
    assert [b["batch_date"] for b in batches] == [
        DAY_TWO.date().isoformat(),
        DAY_ONE.date().isoformat(),
    ]
    assert [b["net_payout"] for b in batches] == ["196.00", "98.00"]


def test_settlement_batch_detail_lists_its_items(client, db) -> None:
    registration = register_merchant(client, email="detail@example.com")
    merchant_id = registration["merchant"]["id"]
    wallet = make_wallet(db)
    for amount in ("100.00", "250.00"):
        make_transaction(
            db, merchant_id=merchant_id, wallet_id=wallet.id, amount=amount, created_at=DAY_ONE
        )
    batch_id = _settle_day(client, registration, DAY_ONE.date().isoformat()).json()["batch_id"]

    response = client.get(
        f"/merchants/{merchant_id}/settlements/{batch_id}",
        headers=auth_header(registration),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transaction_count"] == 2
    assert [item["amount"] for item in body["items"]] == ["100.00", "250.00"]
    assert body["total_amount"] == "350.00"


def test_settlement_history_is_scoped_to_the_merchant(client, db) -> None:
    mine = register_merchant(client, email="mine@example.com")
    other = register_merchant(client, email="other@example.com")
    wallet = make_wallet(db)
    make_transaction(
        db,
        merchant_id=mine["merchant"]["id"],
        wallet_id=wallet.id,
        amount="100.00",
        created_at=DAY_ONE,
    )
    _settle_day(client, mine, DAY_ONE.date().isoformat())

    response = client.get(
        f"/merchants/{other['merchant']['id']}/settlements",
        headers=auth_header(other),
    )
    assert response.status_code == 200
    assert response.json() == []

    mine_batches = client.get(
        f"/merchants/{mine['merchant']['id']}/settlements",
        headers=auth_header(mine),
    ).json()
    batch_id = mine_batches[0]["id"]
    cross_read = client.get(
        f"/merchants/{other['merchant']['id']}/settlements/{batch_id}",
        headers=auth_header(other),
    )
    # 404 rather than 403: confirming the batch exists would leak another
    # merchant's settlement activity.
    assert cross_read.status_code == 404
