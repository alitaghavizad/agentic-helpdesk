from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)
JWT_ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(claims: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "type": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def create_refresh_token(*, subject: str) -> tuple[str, str, datetime]:
    """Returns (raw_token, sha256_hash_for_storage, expires_at)."""
    now = datetime.now(timezone.utc)
    expires_at = now + REFRESH_TOKEN_TTL
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }
    raw = jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, token_hash, expires_at


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM])


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
