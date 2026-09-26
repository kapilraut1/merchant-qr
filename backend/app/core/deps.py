"""Reusable FastAPI dependencies."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db import get_db
from app.models import Merchant

bearer_scheme = HTTPBearer(auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_merchant(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Merchant:
    """Resolve the merchant behind a bearer token.

    Raises 401 when the token is missing, malformed, expired, or points at a
    merchant that no longer exists.
    """
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_EXCEPTION

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise CREDENTIALS_EXCEPTION from exc

    subject = payload.get("sub")
    if subject is None:
        raise CREDENTIALS_EXCEPTION

    try:
        merchant_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise CREDENTIALS_EXCEPTION from exc

    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise CREDENTIALS_EXCEPTION
    return merchant
