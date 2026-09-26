"""Login and JWT-protected endpoint tests."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Merchant

CREDENTIALS = {"email": "ops@everestcoffee.example", "password": "sup3r-secret-pw"}


def _register(client: TestClient) -> dict:
    response = client.post(
        "/merchants/register",
        json={
            "name": "Everest Coffee House",
            "email": CREDENTIALS["email"],
            "password": CREDENTIALS["password"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_login_returns_a_token(client: TestClient) -> None:
    _register(client)

    response = client.post("/merchants/login", json=CREDENTIALS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["merchant"]["email"] == CREDENTIALS["email"]


def test_login_is_case_insensitive_on_email(client: TestClient) -> None:
    _register(client)

    response = client.post(
        "/merchants/login",
        json={"email": CREDENTIALS["email"].upper(), "password": CREDENTIALS["password"]},
    )

    assert response.status_code == 200, response.text


def test_login_with_wrong_password_is_401(client: TestClient) -> None:
    _register(client)

    response = client.post("/merchants/login", json={**CREDENTIALS, "password": "wrong-pw"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_for_unknown_email_is_401(client: TestClient) -> None:
    response = client.post(
        "/merchants/login",
        json={"email": "nobody@example.com", "password": CREDENTIALS["password"]},
    )

    assert response.status_code == 401
    # Identical message to the wrong-password case, so accounts cannot be enumerated.
    assert response.json()["detail"] == "Incorrect email or password"


def test_registered_token_authenticates_immediately(client: TestClient) -> None:
    """Registration hands back a working token, so the dashboard can skip login."""
    token = _register(client)["access_token"]

    response = client.get("/merchants/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    assert response.json()["email"] == CREDENTIALS["email"]


def test_me_requires_a_token(client: TestClient) -> None:
    response = client.get("/merchants/me")

    # 401 rather than FastAPI's default 403: per RFC 6750 a request with no
    # credentials is unauthenticated, not forbidden.
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_me_rejects_a_malformed_token(client: TestClient) -> None:
    response = client.get("/merchants/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


def test_me_rejects_a_token_signed_with_another_secret(client: TestClient) -> None:
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "1", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "not-the-real-secret",
        algorithm=settings.jwt_algorithm,
    )

    response = client.get("/merchants/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_me_rejects_an_expired_token(client: TestClient) -> None:
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "1",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get("/merchants/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


def test_me_rejects_a_token_for_a_deleted_merchant(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """A token outlives the account it points at, so the lookup must still happen."""
    token = _register(client)["access_token"]

    merchant = db.scalar(select(Merchant))
    assert merchant is not None
    merchant_id = merchant.id
    db.delete(merchant)
    db.commit()

    response = client.get("/merchants/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert merchant_id > 0


def test_token_subject_is_the_merchant_id(client: TestClient) -> None:
    body = _register(client)

    settings = get_settings()
    payload = jwt.decode(
        body["access_token"],
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )

    assert payload["sub"] == str(body["merchant"]["id"])


def test_token_expiry_matches_configured_lifetime(client: TestClient) -> None:
    body = _register(client)
    settings = get_settings()

    payload = jwt.decode(
        body["access_token"],
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    lifetime = payload["exp"] - payload["iat"]

    assert lifetime == settings.jwt_expire_minutes * 60


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("password", "short"),
        ("commission_rate", "1.5"),
        ("commission_rate", "-0.01"),
        ("commission_rate", "0.012345"),
    ],
)
def test_registration_validation_errors(client: TestClient, field: str, value: str) -> None:
    payload = {
        "name": "Everest Coffee House",
        "email": CREDENTIALS["email"],
        "password": CREDENTIALS["password"],
        **({field: value} if field == "commission_rate" else {}),
    }
    if field != "commission_rate":
        payload[field] = value

    response = client.post("/merchants/register", json=payload)

    assert response.status_code == 422, response.text
