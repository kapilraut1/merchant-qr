from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MONEY = Numeric(14, 2)


class Wallet(Base):
    """A customer's wallet. The balance is debited when a payment completes."""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Wallets belong to a customer, modelled as the customer id plus a label.
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    # A wallet can be opened against a specific merchant, or globally when NULL.
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    balance: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NPR", server_default="NPR"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    merchant: Mapped["Merchant | None"] = relationship(back_populates="wallets")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Wallet id={self.id} customer={self.customer_id!r} balance={self.balance}>"
