from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_COMMISSION_RATE = Decimal("1")


class MerchantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    commission_rate: Decimal = Field(
        default=Decimal("0.02"),
        ge=Decimal("0"),
        le=MAX_COMMISSION_RATE,
        description="Platform commission as a fraction, e.g. 0.02 for 2%.",
    )

    @field_validator("commission_rate")
    @classmethod
    def _reject_excess_precision(cls, value: Decimal) -> Decimal:
        if -value.as_tuple().exponent > 4:
            raise ValueError("commission_rate supports at most 4 decimal places")
        return value


class MerchantLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    commission_rate: Decimal
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    merchant: MerchantOut
