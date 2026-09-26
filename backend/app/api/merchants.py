"""Merchant registration, login and profile endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import get_current_merchant
from app.core.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import Merchant
from app.schemas import MerchantCreate, MerchantLogin, MerchantOut, TokenResponse

router = APIRouter(prefix="/merchants", tags=["merchants"])


def _issue_token(merchant: Merchant) -> TokenResponse:
    settings = get_settings()
    token = create_access_token(
        subject=str(merchant.id),
        extra_claims={"email": merchant.email},
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        merchant=MerchantOut.model_validate(merchant),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a merchant and return an access token",
)
def register_merchant(
    payload: MerchantCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    email = payload.email.lower()

    existing = db.scalar(select(Merchant).where(Merchant.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A merchant with that email already exists",
        )

    merchant = Merchant(
        name=payload.name,
        email=email,
        password_hash=hash_password(payload.password),
        commission_rate=payload.commission_rate,
    )
    db.add(merchant)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two concurrent registrations for the same email: the unique index is
        # the real source of truth, so translate it into the same 409.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A merchant with that email already exists",
        ) from exc

    db.refresh(merchant)
    return _issue_token(merchant)


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
def login(
    payload: MerchantLogin,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    merchant = db.scalar(select(Merchant).where(Merchant.email == payload.email.lower()))
    if merchant is None or not verify_password(payload.password, merchant.password_hash):
        # Same message either way so the endpoint cannot enumerate accounts.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_token(merchant)


@router.get("/me", response_model=MerchantOut, summary="The authenticated merchant")
def read_current_merchant(
    current: Annotated[Merchant, Depends(get_current_merchant)],
) -> MerchantOut:
    return MerchantOut.model_validate(current)
