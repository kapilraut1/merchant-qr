"""Shared pytest fixtures.

Tests run against a real PostgreSQL instance so that the schema, the money
arithmetic and the idempotency constraints behave exactly as they do in
production. A dedicated `<database>_test` database is created once per session
and each test runs inside a transaction that is rolled back afterwards.
"""

import os
from collections.abc import Generator, Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, get_db

TEST_DATABASE_SUFFIX = "_test"


def _test_url() -> "object":
    """Derive the test database URL from the configured application URL.

    An explicit ``TEST_DATABASE_URL`` always wins, which is what CI uses.
    """
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return make_url(explicit)

    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}{TEST_DATABASE_SUFFIX}")


def _ensure_database(url) -> None:
    """Create the test database if it does not exist yet."""
    dsn = (
        f"host={url.host} port={url.port or 5432} "
        f"user={url.username} password={url.password} dbname=postgres"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if row is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))


def _drop_database(url) -> None:
    dsn = (
        f"host={url.host} port={url.port or 5432} "
        f"user={url.username} password={url.password} dbname=postgres"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(url.database))
        )


@pytest.fixture(scope="session")
def engine():
    url = _test_url()
    _ensure_database(url)

    test_engine = create_engine(url, pool_pre_ping=True, future=True)
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        _drop_database(url)


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:
    """A session whose work is rolled back at the end of the test.

    ``join_transaction_mode="create_savepoint"`` is essential: the application
    calls ``session.commit()`` inside request handlers, and without a savepoint
    those commits would also commit the outer transaction, leaking rows into
    every subsequent test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db) -> Iterator:
    """FastAPI test client wired to the transaction-rolled-back test session."""
    from fastapi.testclient import TestClient

    from app.main import app

    def override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
