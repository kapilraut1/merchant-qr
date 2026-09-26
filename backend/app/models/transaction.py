import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MONEY = Numeric(14, 2)


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


class Transaction(Base):
    """A single QR payment attempt against a merchant.

    ``idempotency_key`` carries a UNIQUE constraint: it is the mechanism that
    makes a retried payment safe, so the database -- not application-level
    "check then insert" -- is the final arbiter under concurrency.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        # The settlement job scans a merchant's completed transactions for a day.
        Index("ix_transactions_merchant_status_created", "merchant_id", "status", "created_at"),
        # Named explicitly so tests can drop it to simulate a corrupted or
        # legacy-imported table and exercise duplicate-key reconciliation.
        UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            name="transaction_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=TransactionStatus.PENDING,
        server_default=TransactionStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    merchant: Mapped["Merchant"] = relationship()  # noqa: F821
    wallet: Mapped["Wallet"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Transaction id={self.id} merchant={self.merchant_id} "
            f"amount={self.amount} status={self.status.value}>"
        )
