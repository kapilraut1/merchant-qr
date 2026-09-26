"""Wallet creation and balance tests."""

from decimal import Decimal

from fastapi.testclient import TestClient


def _register_merchant(client: TestClient) -> dict:
    response = client.post(
        "/merchants/register",
        json={
            "name": "Everest Coffee House",
            "email": "ops@everestcoffee.example",
            "password": "sup3r-secret-pw",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["merchant"]


def test_create_wallet_with_zero_balance(client: TestClient) -> None:
    response = client.post("/wallets", json={"customer_id": "cust-001"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer_id"] == "cust-001"
    assert Decimal(body["balance"]) == Decimal("0.00")
    assert body["currency"] == "NPR"
    assert body["merchant_id"] is None


def test_create_wallet_with_an_opening_balance(client: TestClient) -> None:
    response = client.post(
        "/wallets",
        json={"customer_id": "cust-002", "label": "Aisha", "initial_balance": "5000.50"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(body["balance"]) == Decimal("5000.50")
    assert body["label"] == "Aisha"


def test_create_wallet_bound_to_a_known_merchant(client: TestClient) -> None:
    merchant = _register_merchant(client)

    response = client.post(
        "/wallets",
        json={"customer_id": "cust-003", "merchant_id": merchant["id"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["merchant_id"] == merchant["id"]


def test_create_wallet_for_unknown_merchant_is_404(client: TestClient) -> None:
    response = client.post(
        "/wallets",
        json={"customer_id": "cust-004", "merchant_id": 999_999},
    )

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_negative_opening_balance_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/wallets",
        json={"customer_id": "cust-005", "initial_balance": "-10.00"},
    )

    assert response.status_code == 422


def test_get_wallet_returns_the_balance(client: TestClient) -> None:
    created = client.post(
        "/wallets", json={"customer_id": "cust-006", "initial_balance": "120.00"}
    ).json()

    response = client.get(f"/wallets/{created['id']}")

    assert response.status_code == 200
    assert Decimal(response.json()["balance"]) == Decimal("120.00")


def test_balance_endpoint_matches_the_wallet_endpoint(client: TestClient) -> None:
    created = client.post(
        "/wallets", json={"customer_id": "cust-007", "initial_balance": "77.25"}
    ).json()

    wallet = client.get(f"/wallets/{created['id']}").json()
    balance = client.get(f"/wallets/{created['id']}/balance").json()

    assert wallet == balance


def test_missing_wallet_is_404(client: TestClient) -> None:
    response = client.get("/wallets/424242")

    assert response.status_code == 404
    assert response.json()["detail"] == "Wallet not found"


def test_top_up_increases_the_balance(client: TestClient) -> None:
    created = client.post(
        "/wallets", json={"customer_id": "cust-008", "initial_balance": "10.00"}
    ).json()

    response = client.post(f"/wallets/{created['id']}/top-up", json={"amount": "32.10"})

    assert response.status_code == 200, response.text
    assert Decimal(response.json()["balance"]) == Decimal("42.10")


def test_top_up_is_visible_on_a_following_read(client: TestClient) -> None:
    created = client.post("/wallets", json={"customer_id": "cust-009"}).json()
    client.post(f"/wallets/{created['id']}/top-up", json={"amount": "15.00"})

    reread = client.get(f"/wallets/{created['id']}").json()

    assert Decimal(reread["balance"]) == Decimal("15.00")


def test_top_up_of_zero_or_negative_is_rejected(client: TestClient) -> None:
    created = client.post("/wallets", json={"customer_id": "cust-010"}).json()

    assert client.post(f"/wallets/{created['id']}/top-up", json={"amount": "0"}).status_code == 422
    assert client.post(f"/wallets/{created['id']}/top-up", json={"amount": "-5"}).status_code == 422


def test_top_up_on_missing_wallet_is_404(client: TestClient) -> None:
    response = client.post("/wallets/999999/top-up", json={"amount": "10.00"})

    assert response.status_code == 404
