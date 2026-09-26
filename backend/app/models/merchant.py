from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# A merchant's platform commission, stored as a fraction: 0.02 == 2%.
COMMISSION_RATE_PRECISION = Numeric(5, 4)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    # Never the plaintext: see app.core.security.hash_password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(
        COMMISSION_RATE_PRECISION,
        nullable=False,
        default=Decimal("0.02"),
        server_default=text("0.0200"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    wallets: Mapped[list["Wallet"]] = relationship(back_populates="merchant")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Merchant id={self.id} email={self.email!r}>"
