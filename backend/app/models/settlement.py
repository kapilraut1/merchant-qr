import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MONEY = Numeric(14, 2)


class SettlementStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class SettlementBatch(Base):
    """One merchant's settled total for a single day.

    ``total_amount`` is stored as the sum of the batch's items, and
    ``commission_deducted`` / ``net_payout`` are frozen at settlement time. If
    the merchant's commission rate changes later, historical batches must not
    move -- which is why the money is denormalised here rather than derived on
    read.
    """

    __tablename__ = "settlement_batches"
    __table_args__ = (
        # Lets the reconciliation report and the dashboard read a merchant's
        # history newest-first without a sort.
        Index("ix_settlement_batches_merchant_date", "merchant_id", "batch_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    commission_deducted: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_payout: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[SettlementStatus] = mapped_column(
        Enum(
            SettlementStatus,
            name="settlement_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SettlementStatus.COMPLETED,
        server_default=SettlementStatus.COMPLETED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    merchant: Mapped["Merchant"] = relationship()  # noqa: F821
    items: Mapped[list["SettlementItem"]] = relationship(  # noqa: F821
        back_populates="batch", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<SettlementBatch id={self.id} merchant={self.merchant_id} "
            f"date={self.batch_date} total={self.total_amount} net={self.net_payout}>"
        )


class SettlementItem(Base):
    """The join between a settled transaction and the batch that paid for it.

    ``amount`` is a snapshot of the transaction amount so that a batch's items
    can be summed without joining back to ``transactions``.

    The UNIQUE constraint on ``transaction_id`` is what guarantees a
    transaction is settled at most once -- the same guarantee the idempotency
    index gives on the payment side.
    """

    __tablename__ = "settlement_items"
    __table_args__ = (UniqueConstraint("transaction_id", name="uq_settlement_items_transaction"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("settlement_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    batch: Mapped["SettlementBatch"] = relationship(back_populates="items")  # noqa: F821
    transaction: Mapped["Transaction"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<SettlementItem id={self.id} batch={self.batch_id} amount={self.amount}>"
