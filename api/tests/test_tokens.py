"""Access and refresh token primitives."""

from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from app.errors import TokenExpired, TokenInvalid
from app.security.tokens import (
    AccessClaims,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
    issue_access_token,
)

SECRET = "a-test-signing-secret-of-at-least-32-bytes"


def _issue(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "secret": SECRET,
        "user_id": uuid4(),
        "role": "member",
        "token_version": 0,
        "ttl_seconds": 900,
    }
    kwargs.update(overrides)
    token, _ = issue_access_token(**kwargs)  # type: ignore[arg-type]
    return token


def test_a_token_round_trips_its_claims() -> None:
    user_id = uuid4()
    token = _issue(user_id=user_id, role="admin", token_version=7)
    claims = decode_access_token(secret=SECRET, token=token)
    assert isinstance(claims, AccessClaims)
    assert claims.user_id == user_id
    assert claims.role == "admin"
    assert claims.token_version == 7
    assert claims.expires_at > datetime.now(UTC)


def test_a_tampered_token_is_rejected() -> None:
    token = _issue()
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(TokenInvalid):
        decode_access_token(secret=SECRET, token=tampered)


def test_a_token_signed_with_another_secret_is_rejected() -> None:
    with pytest.raises(TokenInvalid):
        decode_access_token(secret="a-completely-different-secret-also-32-plus", token=_issue())


def test_an_expired_token_is_distinguishable_from_an_invalid_one() -> None:
    """A client should know to refresh rather than to re-authenticate."""
    expired = _issue(ttl_seconds=-1)
    with pytest.raises(TokenExpired):
        decode_access_token(secret=SECRET, token=expired)


def test_a_token_missing_our_claims_is_invalid_not_a_crash() -> None:
    """Signed by us but not one of ours: reject, do not raise KeyError."""
    foreign = jwt.encode({"sub": str(uuid4()), "exp": 9999999999}, SECRET, algorithm="HS256")
    with pytest.raises(TokenInvalid):
        decode_access_token(secret=SECRET, token=foreign)


def test_the_algorithm_is_pinned() -> None:
    """A token claiming alg=none must not be accepted."""
    unsigned = jwt.encode(
        {"sub": str(uuid4()), "role": "admin", "token_version": 0, "jti": "x", "exp": 9999999999},
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenInvalid):
        decode_access_token(secret=SECRET, token=unsigned)


def test_refresh_tokens_are_random_and_hashed() -> None:
    first_plain, first_digest = generate_refresh_token()
    second_plain, second_digest = generate_refresh_token()

    assert first_plain != second_plain
    assert first_digest != second_digest
    assert len(first_digest) == 32
    # The digest is what is stored; the plaintext must not be recoverable from it.
    assert first_plain.encode() not in first_digest


def test_hashing_is_deterministic_so_lookup_works() -> None:
    plaintext, digest = generate_refresh_token()
    assert hash_refresh_token(plaintext) == digest
