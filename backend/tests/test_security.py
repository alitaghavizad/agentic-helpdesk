from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.security import (
    create_access_token, create_refresh_token, decode_token,
    hash_password, hash_refresh_token, verify_password,
)
from app.config import get_settings


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hash_is_salted_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b


def test_access_token_roundtrip():
    token = create_access_token({"sub": "user-123", "role": "employee", "kind": "user"})
    claims = decode_token(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "employee"
    assert claims["type"] == "access"


def test_refresh_token_hash_matches_stored_hash():
    raw, token_hash, expires_at = create_refresh_token(subject="user-123")
    assert hash_refresh_token(raw) == token_hash
    claims = decode_token(raw)
    assert claims["type"] == "refresh"
    assert claims["exp"] > claims["iat"]
    assert expires_at.timestamp() == pytest.approx(claims["exp"], abs=2)


def test_tampered_token_is_rejected():
    """Tamper a character in the MIDDLE of the signature, not the last one.

    The signature is base64url, and its final character can carry fewer
    than 6 significant bits (the encoded byte length decides how many);
    when it does, substituting it decodes to the identical byte string, so
    the "tampered" token still verifies and the test fails spuriously. This
    is what made this test intermittently red -- it was mislabelled a flake
    for two phases. A mid-string base64 character always contributes a full
    6 bits, so altering one always changes the decoded signature.
    """
    token = create_access_token({"sub": "user-123", "role": "employee", "kind": "user"})
    header_payload, _, signature = token.rpartition(".")
    midpoint = len(signature) // 2
    swapped = "A" if signature[midpoint] != "A" else "B"
    tampered = f"{header_payload}.{signature[:midpoint]}{swapped}{signature[midpoint + 1:]}"

    assert tampered != token
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(tampered)


def test_expired_token_is_rejected():
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": "user-123", "role": "employee", "type": "access",
        "iat": now - timedelta(minutes=30), "exp": now - timedelta(minutes=15),
    }
    expired_token = jwt.encode(expired_payload, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired_token)
