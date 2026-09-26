"""Merchant registration tests."""

from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Merchant

VALID_PAYLOAD = {
    "name": "Everest Coffee House",
    "email": "ops@everestcoffee.example",
    "password": "sup3r-secret-pw",
}


def test_register_returns_201_with_token_and_merchant(client: TestClient) -> None:
    response = client.post("/merchants/register", json=VALID_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["merchant"]["name"] == "Everest Coffee House"
    assert body["merchant"]["email"] == "ops@everestcoffee.example"
    assert body["merchant"]["id"] > 0
    # Pydantic renders Decimal as a JSON string to avoid float rounding.
    assert Decimal(body["merchant"]["commission_rate"]) == Decimal("0.02")


def test_register_persists_the_merchant(
    client: TestClient,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    client.post("/merchants/register", json=VALID_PAYLOAD)

    db.expire_all()
    merchant = db.scalar(select(Merchant))
    assert merchant is not None
    assert merchant.email == "ops@everestcoffee.example"
    assert merchant.commission_rate == Decimal("0.0200")
    assert "sup3r-secret-pw" not in merchant.password_hash


def test_register_honours_a_custom_commission_rate(client: TestClient) -> None:
    response = client.post(
        "/merchants/register",
        json={**VALID_PAYLOAD, "commission_rate": "0.015"},
    )

    assert response.status_code == 201, response.text
    assert Decimal(response.json()["merchant"]["commission_rate"]) == Decimal("0.015")


def test_register_lowercases_the_email(client: TestClient) -> None:
    response = client.post(
        "/merchants/register",
        json={**VALID_PAYLOAD, "email": "Ops@EverestCoffee.Example"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["merchant"]["email"] == "ops@everestcoffee.example"


def test_duplicate_email_is_rejected_with_409(client: TestClient) -> None:
    first = client.post("/merchants/register", json=VALID_PAYLOAD)
    assert first.status_code == 201

    duplicate = client.post(
        "/merchants/register",
        json={**VALID_PAYLOAD, "name": "Impostor Ltd"},
    )

    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    assert client.post("/merchants/register", json=VALID_PAYLOAD).status_code == 201

    duplicate = client.post(
        "/merchants/register",
        json={**VALID_PAYLOAD, "email": "OPS@EVERESTCOFFEE.EXAMPLE"},
    )

    assert duplicate.status_code == 409


def test_response_never_leaks_the_password(client: TestClient) -> None:
    response = client.post("/merchants/register", json=VALID_PAYLOAD)

    assert "sup3r-secret-pw" not in response.text
    assert "password" not in response.json()["merchant"]
