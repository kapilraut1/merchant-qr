"""Payment processing with idempotent retries.

The contract is the one every payment gateway offers:

    submitting the same ``idempotency_key`` twice must produce exactly one
    transaction, and the second call must return that same transaction.

Correctness under concurrency rests on three things:

1. a UNIQUE index on ``transactions.idempotency_key``, so the database -- not an
   application-level "check then insert" -- breaks the tie;
2. the losing request rolling its whole attempt back and re-reading the winner,
   which leaves the session in a usable state and guarantees no partial write;
3. ``SELECT ... FOR UPDATE`` on the wallet row, so two *different* payments
   racing for the same balance cannot both pass the funds check.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.invoices import InvalidInvoiceToken, InvoiceClaims, verify_invoice_token
from app.models import Merchant, Transaction, TransactionStatus, Wallet


class PaymentError(Exception):
    """Base class for payment failures that map onto an HTTP response."""


class UnknownMerchantError(PaymentError):
    pass


class UnknownWalletError(PaymentError):
    pass


class IdempotencyKeyConflictError(PaymentError):
    """The key was already used, but for a different merchant."""


class InvoiceMismatchError(PaymentError):
    """The supplied invoice does not describe this payment."""


@dataclass(frozen=True)
class PaymentResult:
    transaction: Transaction
    replayed: bool


def process_payment(
    db: Session,
    *,
    merchant_id: int,
    wallet_id: int,
    amount: Decimal,
    idempotency_key: str,
    invoice_claims: InvoiceClaims | None = None,
) -> PaymentResult:
    """Record a payment attempt exactly once for ``idempotency_key``.

    A replay returns the original transaction untouched, even if the request
    body differs -- that is what makes a client retry safe. The one exception is
    a key replayed by a *different* merchant, which is rejected rather than
    echoed back, so keys cannot be probed across merchants.
    """
    if invoice_claims is not None:
        if invoice_claims.merchant_id != merchant_id or invoice_claims.amount != amount:
            raise InvoiceMismatchError("invoice does not match the requested merchant and amount")

    existing = db.scalar(select(Transaction).where(Transaction.idempotency_key == idempotency_key))
    if existing is not None:
        return _replay(existing, merchant_id)

    if db.get(Merchant, merchant_id) is None:
        raise UnknownMerchantError(f"merchant {merchant_id} does not exist")

    # Lock the wallet so concurrent payments serialise on the balance check.
    wallet = db.scalar(select(Wallet).where(Wallet.id == wallet_id).with_for_update())
    if wallet is None:
        raise UnknownWalletError(f"wallet {wallet_id} does not exist")

    # Decide the outcome while the wallet row is still locked, so the funds
    # check and the debit cannot be separated by another transaction.
    balance = wallet.balance or Decimal("0.00")
    is_funded = balance >= amount

    transaction = Transaction(
        merchant_id=merchant_id,
        wallet_id=wallet_id,
        amount=amount,
        idempotency_key=idempotency_key,
        status=TransactionStatus.COMPLETED if is_funded else TransactionStatus.FAILED,
    )
    db.add(transaction)
    if is_funded:
        wallet.balance = balance - amount

    try:
        # One commit covers the ledger row and the debit together, so a payment
        # can never be recorded without the matching funds movement.
        db.commit()
    except IntegrityError:
        # Another request inserted this key first. Discard our attempt whole
        # (including the debit we staged) and hand back the row that won.
        db.rollback()
        winner = db.scalar(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        )
        if winner is None:  # pragma: no cover - constraint was something else
            raise
        return _replay(winner, merchant_id)

    db.refresh(transaction)
    return PaymentResult(transaction=transaction, replayed=False)


def _replay(existing: Transaction, merchant_id: int) -> PaymentResult:
    if existing.merchant_id != merchant_id:
        raise IdempotencyKeyConflictError(
            "idempotency_key has already been used by another merchant"
        )
    return PaymentResult(transaction=existing, replayed=True)


def resolve_invoice(
    token: str | None, *, merchant_id: int, amount: Decimal
) -> InvoiceClaims | None:
    """Validate an optional invoice token against the payment being attempted."""
    if token is None:
        return None
    try:
        return verify_invoice_token(token)
    except InvalidInvoiceToken as exc:
        raise InvoiceMismatchError(str(exc)) from exc
