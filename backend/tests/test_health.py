from fastapi.testclient import TestClient


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "merchant-qr-settlement"
    assert "version" in body


def test_health_does_not_touch_the_database(client: TestClient) -> None:
    """/health is a pure liveness probe, so it must succeed even when the
    database connection is unusable."""
    from app.db import get_db

    def broken_db():
        class BrokenSession:
            def execute(self, *args, **kwargs):
                raise RuntimeError("database is down")

        yield BrokenSession()

    client.app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/health")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_confirms_database_connectivity(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_root_banner(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "merchant-qr-settlement"
    assert body["health"] == "/health"


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
