"""Wallet endpoints.

Wallets stand in for the customer-facing side of the wallet app, so these
routes are deliberately unauthenticated: there is no customer identity
provider in this simulation. Merchants are the authenticated actors.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Merchant, Wallet
from app.schemas import WalletCreate, WalletOut, WalletTopUp

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.post("", response_model=WalletOut, status_code=status.HTTP_201_CREATED)
def create_wallet(
    payload: WalletCreate,
    db: Annotated[Session, Depends(get_db)],
) -> WalletOut:
    if payload.merchant_id is not None and db.get(Merchant, payload.merchant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Merchant {payload.merchant_id} does not exist",
        )

    wallet = Wallet(
        customer_id=payload.customer_id,
        label=payload.label,
        balance=payload.initial_balance,
        merchant_id=payload.merchant_id,
    )
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return WalletOut.model_validate(wallet)


@router.get("/{wallet_id}", response_model=WalletOut)
def get_wallet(
    wallet_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletOut:
    wallet = db.get(Wallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")
    return WalletOut.model_validate(wallet)


@router.get("/{wallet_id}/balance", response_model=WalletOut)
def get_wallet_balance(
    wallet_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletOut:
    return get_wallet(wallet_id, db)


@router.post("/{wallet_id}/top-up", response_model=WalletOut)
def top_up_wallet(
    payload: WalletTopUp,
    wallet_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletOut:
    """Demo-only helper so a fresh wallet can be funded without a payment rail."""
    wallet = db.get(Wallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")

    wallet.balance = (wallet.balance or Decimal("0.00")) + payload.amount
    db.commit()
    db.refresh(wallet)
    return WalletOut.model_validate(wallet)
