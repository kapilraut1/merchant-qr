"""Concurrency tests for the idempotency guarantee.

These use real threads, real connections and real commits, because the claim
being tested -- "concurrent retries of the same key produce exactly one
transaction" -- only means something when requests actually overlap.

Seeded rows are committed and then deleted in teardown: the worker threads need
to *see* the merchant and wallet, which is impossible with uncommitted setup
data, and leftovers would break the suite's other tests that assert on table
counts.
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from tests.factories import make_merchant, make_wallet

from app.models import Merchant, Transaction, TransactionStatus, Wallet
from app.services.payments import process_payment

THREADS = 8


@pytest.fixture
def engine_session(engine) -> Iterator[Session]:
    """A session that genuinely commits, for fixture data."""
    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def connections(engine) -> Iterator[list]:
    """THREADS independent connections.

    No transaction is pre-begun: each worker commits for real, because the only
    way to observe the final balance is for the debits to actually be durable.
    Teardown removes the rows instead of rolling them back.
    """
    opened = [engine.connect() for _ in range(THREADS)]
    try:
        yield opened
    finally:
        for connection in opened:
            connection.close()


@pytest.fixture
def funded_party(engine_session: Session):
    """Commit a merchant and a wallet, then remove them afterwards."""
    merchant = make_merchant(engine_session, email="concurrency@example.com")
    wallet = make_wallet(engine_session, balance="1000.00", merchant_id=merchant.id)
    merchant_id, wallet_id = merchant.id, wallet.id
    engine_session.commit()

    try:
        yield merchant_id, wallet_id
    finally:
        engine_session.execute(delete(Transaction).where(Transaction.merchant_id == merchant_id))
        engine_session.execute(delete(Wallet).where(Wallet.id == wallet_id))
        engine_session.execute(delete(Merchant).where(Merchant.id == merchant_id))
        engine_session.commit()


def _session(connection) -> Session:
    return Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


def _run_concurrently(connections, barrier, work) -> list:
    """Fire ``work(index)`` on every connection, released together."""

    def run(index: int):
        barrier.wait(timeout=15)
        return work(index)

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        return list(pool.map(run, range(THREADS)))


def test_concurrent_same_key_creates_exactly_one_transaction(
    engine,
    engine_session,
    connections,
    funded_party,
) -> None:
    merchant_id, wallet_id = funded_party

    def work(index: int):
        session = _session(connections[index])
        try:
            result = process_payment(
                session,
                merchant_id=merchant_id,
                wallet_id=wallet_id,
                amount=Decimal("100.00"),
                idempotency_key="concurrent-key",
            )
            return result.transaction.id, result.replayed
        finally:
            session.close()

    # The barrier lines every thread up so they all pass the "does this key
    # already exist?" check before any of them reaches the INSERT. That is the
    # race the unique constraint plus the SAVEPOINT exist to survive.
    outcomes = _run_concurrently(connections, threading.Barrier(THREADS), work)

    transaction_ids = {transaction_id for transaction_id, _ in outcomes}
    assert len(transaction_ids) == 1, f"expected one transaction, got {transaction_ids}"

    fresh = [replayed for _, replayed in outcomes if not replayed]
    assert len(fresh) == 1, "exactly one caller should report a newly created transaction"
    assert len(outcomes) - len(fresh) == THREADS - 1, "everyone else must see a replay"


def test_concurrent_same_key_debits_the_wallet_once(
    engine,
    engine_session,
    connections,
    funded_party,
) -> None:
    merchant_id, wallet_id = funded_party

    def work(index: int):
        session = _session(connections[index])
        try:
            process_payment(
                session,
                merchant_id=merchant_id,
                wallet_id=wallet_id,
                amount=Decimal("100.00"),
                idempotency_key="concurrent-debit",
            )
        finally:
            session.close()

    _run_concurrently(connections, threading.Barrier(THREADS), work)

    engine_session.expire_all()
    balance = engine_session.scalar(select(Wallet.balance).where(Wallet.id == wallet_id))
    count = engine_session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.idempotency_key == "concurrent-debit")
    )

    assert balance == Decimal("900.00"), f"balance debited more than once: {balance}"
    assert count == 1


def test_concurrent_distinct_keys_respect_the_balance(
    engine,
    engine_session,
    connections,
) -> None:
    """Distinct keys are distinct payments, so the balance must still add up."""
    merchant = make_merchant(engine_session, email="overspend@example.com")
    # Only enough funds for exactly three 100.00 payments.
    wallet = make_wallet(engine_session, balance="300.00", merchant_id=merchant.id)
    merchant_id, wallet_id = merchant.id, wallet.id
    engine_session.commit()

    def work(index: int):
        session = _session(connections[index])
        try:
            process_payment(
                session,
                merchant_id=merchant_id,
                wallet_id=wallet_id,
                amount=Decimal("100.00"),
                idempotency_key=f"distinct-{index}",
            )
        finally:
            session.close()

    try:
        _run_concurrently(connections, threading.Barrier(THREADS), work)

        engine_session.expire_all()
        balance = engine_session.scalar(select(Wallet.balance).where(Wallet.id == wallet_id))
        completed = engine_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.merchant_id == merchant_id,
                Transaction.status == TransactionStatus.COMPLETED,
            )
        )
        failed = engine_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.merchant_id == merchant_id,
                Transaction.status == TransactionStatus.FAILED,
            )
        )

        # SELECT ... FOR UPDATE on the wallet row is what makes this exact
        # rather than a race that lets the balance go negative.
        assert balance == Decimal("0.00"), f"balance went negative: {balance}"
        assert completed == 3
        assert failed == THREADS - 3
    finally:
        engine_session.execute(delete(Transaction).where(Transaction.merchant_id == merchant_id))
        engine_session.execute(delete(Wallet).where(Wallet.id == wallet_id))
        engine_session.execute(delete(Merchant).where(Merchant.id == merchant_id))
        engine_session.commit()
