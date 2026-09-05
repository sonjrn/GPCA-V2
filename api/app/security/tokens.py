"""Access and refresh token primitives.

Access tokens are short-lived JWTs the API can verify without a database read
(beyond the `token_version` check). Refresh tokens are opaque random strings
stored as SHA-256 digests -- they are looked up, not verified, so there is
nothing in the database an attacker could replay.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt

# 256 bits of entropy. Long enough that guessing is not a strategy.
REFRESH_TOKEN_BYTES = 32

ALGORITHM = "HS256"


class TokenError(Exception):
    """Base for anything wrong with a presented token."""


class TokenExpired(TokenError):
    """Distinguished from other failures so a client knows to refresh."""


class TokenInvalid(TokenError):
    """Malformed, wrong signature, or wrong shape."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UUID
    role: str
    token_version: int
    jti: str
    expires_at: datetime


def issue_access_token(
    *,
    secret: str,
    user_id: UUID,
    role: str,
    token_version: int,
    ttl_seconds: int,
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        # Carried in the token so a role change or suspension can be detected
        # without waiting for the token to expire.
        "token_version": token_version,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires_at


def decode_access_token(*, secret: str, token: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("access token has expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid("access token is not valid") from exc

    try:
        return AccessClaims(
            user_id=UUID(payload["sub"]),
            role=payload["role"],
            token_version=int(payload["token_version"]),
            jti=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        # A structurally valid but wrongly-shaped token: signed by us, but not
        # one of ours. Treat as invalid rather than crashing on a KeyError.
        raise TokenInvalid("access token is missing expected claims") from exc


def generate_refresh_token() -> tuple[str, bytes]:
    """Return the plaintext to hand the client, and the digest to store.

    The plaintext is returned exactly once and never persisted.
    """
    plaintext = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> bytes:
    """SHA-256, not Argon2.

    A refresh token is 256 bits of uniform randomness, so there is no dictionary
    to attack and no need for a slow hash -- which matters, because this runs on
    every refresh.
    """
    return hashlib.sha256(plaintext.encode()).digest()
