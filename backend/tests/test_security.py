"""Unit tests for password hashing and JWT helpers."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

PASSWORD = "correct horse battery staple"


def test_hash_and_verify_round_trip() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong password", stored) is False


def test_hash_is_salted_so_equal_passwords_differ() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second, "identical passwords must not produce identical hashes"
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


def test_hash_format_is_self_describing() -> None:
    algorithm, iterations, salt, digest = hash_password(PASSWORD).split("$")

    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= 100_000
    assert len(salt) == 32  # 16 bytes hex-encoded
    assert len(digest) == 64  # 32 bytes hex-encoded


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$abc$zz$zz",
        "bcrypt$240000$aa$bb",
        "pbkdf2_sha256$240000$onlythree",
    ],
)
def test_malformed_hashes_are_rejected_without_raising(malformed: str) -> None:
    assert verify_password(PASSWORD, malformed) is False


def test_token_round_trip_carries_subject() -> None:
    token = create_access_token(subject="42", extra_claims={"email": "a@b.com"})

    payload = decode_access_token(token)

    assert payload["sub"] == "42"
    assert payload["email"] == "a@b.com"
    assert "exp" in payload and "iat" in payload


def test_token_signed_with_another_secret_is_rejected() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "1", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "a-different-secret",
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(jwt.PyJWTError):
        decode_access_token(forged)


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "1",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(jwt.PyJWTError):
        decode_access_token("this.is.not-a-jwt")
