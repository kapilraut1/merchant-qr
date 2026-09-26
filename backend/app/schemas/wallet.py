from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class WalletCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=120)
    initial_balance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    merchant_id: int | None = None


class WalletTopUp(BaseModel):
    amount: Decimal = Field(gt=Decimal("0"))


class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: str
    merchant_id: int | None
    label: str | None
    balance: Decimal
    currency: str
    created_at: datetime
