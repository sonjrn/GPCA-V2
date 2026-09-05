"""Email verification and resend."""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select

from app import create_app
from app.security.tokens import hash_refresh_token, issue_access_token
from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, User
from gpca_db.session import build_engine, build_session_factory
from tests.conftest import TEST_SECRET, make_settings

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


@pytest.fixture
def live_app() -> Iterator[Flask]:
    app = create_app(make_settings(database_url=DATABASE_URL))
    engine = build_engine(DATABASE_URL or "")
    factory = build_session_factory(engine)
    with factory() as session:
        session.query(AuthToken).delete()
        session.query(User).delete()
        session.commit()
    yield app
    engine.dispose()


@pytest.fixture
def client(live_app: Flask) -> FlaskClient:
    return live_app.test_client()


def _register(client: FlaskClient, email: str = "ada@example.org") -> None:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "a-perfectly-good-passphrase",
            "first_name": "Ada",
            "last_name": "Byron",
        },
    )


def _issue_token(app: Flask, user: User, purpose: AuthTokenPurpose, *, ttl_hours: int = 24) -> str:
    import secrets

    plaintext = secrets.token_urlsafe(32)
    with app.extensions["gpca_session_factory"]() as session:
        session.add(
            AuthToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_refresh_token(plaintext),
                expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
            )
        )
        session.commit()
    return plaintext


def _only_user(app: Flask) -> User:
    with app.extensions["gpca_session_factory"]() as session:
        return session.scalars(select(User)).one()


def _make_user(app: Flask, **fields: object) -> User:
    defaults: dict[str, object] = {
        "email": "solo@example.org",
        "password_hash": "x",
        "first_name": "Ada",
        "last_name": "Byron",
        "role": UserRole.VIEWER,
        "status": UserStatus.ACTIVE,
        "token_version": 0,
    }
    defaults.update(fields)
    with app.extensions["gpca_session_factory"]() as session:
        user = User(**defaults)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _auth(user: User) -> dict[str, str]:
    token, _ = issue_access_token(
        secret=TEST_SECRET,
        user_id=user.id,
        role=user.role.value,
        token_version=user.token_version,
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


def _verify(client: FlaskClient, token: str):
    return client.post("/api/v1/auth/verify-email", json={"token": token})


def test_a_valid_token_verifies_the_address(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _issue_token(live_app, user, AuthTokenPurpose.EMAIL_VERIFY)

    assert _verify(client, token).status_code == 200
    assert _only_user(live_app).email_verified_at is not None


def test_the_token_is_marked_consumed(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _issue_token(live_app, user, AuthTokenPurpose.EMAIL_VERIFY)
    _verify(client, token)

    with live_app.extensions["gpca_session_factory"]() as session:
        row = session.scalars(
            select(AuthToken).where(AuthToken.token_hash == hash_refresh_token(token))
        ).one()
    assert row.consumed_at is not None


def test_a_token_cannot_be_reused(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _issue_token(live_app, user, AuthTokenPurpose.EMAIL_VERIFY)

    assert _verify(client, token).status_code == 200
    assert _verify(client, token).status_code == 400


def test_an_expired_token_is_rejected(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _issue_token(live_app, user, AuthTokenPurpose.EMAIL_VERIFY, ttl_hours=-1)
    assert _verify(client, token).status_code == 400


def test_an_unknown_token_reveals_nothing(client: FlaskClient) -> None:
    response = _verify(client, "a-token-that-was-never-issued")
    assert response.status_code == 400
    body = json.loads(response.data)["detail"]
    assert "not valid" in body
    assert "expired" not in body and "exist" not in body


def test_a_password_reset_token_cannot_verify_an_email(
    live_app: Flask, client: FlaskClient
) -> None:
    """purpose is checked explicitly, not inferred from the endpoint called."""
    user = _make_user(live_app)
    token = _issue_token(live_app, user, AuthTokenPurpose.PASSWORD_RESET)

    assert _verify(client, token).status_code == 400
    assert _only_user(live_app).email_verified_at is None


def test_resend_requires_authentication(client: FlaskClient) -> None:
    """Otherwise it is a way to mail an arbitrary address on demand."""
    assert client.post("/api/v1/auth/verify-email/resend").status_code == 401


def test_resend_issues_a_new_token_and_kills_the_old_one(
    live_app: Flask, client: FlaskClient
) -> None:
    user = _make_user(live_app)
    first = _issue_token(live_app, user, AuthTokenPurpose.EMAIL_VERIFY)

    assert client.post("/api/v1/auth/verify-email/resend", headers=_auth(user)).status_code == 202

    # The previous link must stop working, or every resend leaves another
    # valid token in circulation.
    assert _verify(client, first).status_code == 400

    with live_app.extensions["gpca_session_factory"]() as session:
        live = session.scalar(
            select(func.count()).select_from(AuthToken).where(AuthToken.consumed_at.is_(None))
        )
    assert live == 1


def test_resend_on_a_verified_account_is_a_no_op(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app, email_verified_at=datetime.now(UTC))
    assert client.post("/api/v1/auth/verify-email/resend", headers=_auth(user)).status_code == 202
    with live_app.extensions["gpca_session_factory"]() as session:
        assert session.scalar(select(func.count()).select_from(AuthToken)) == 0


def test_both_endpoints_are_documented(client: FlaskClient) -> None:
    paths = json.loads(client.get("/api/v1/openapi.json").data)["paths"]
    assert "/api/v1/auth/verify-email" in paths
    assert "/api/v1/auth/verify-email/resend" in paths


def test_registration_leaves_exactly_one_live_verification_token(
    live_app: Flask, client: FlaskClient
) -> None:
    """The partial unique index, observed from the outside.

    These tests originally tried to add a second live token after registering
    and hit the constraint -- which is the constraint doing its job.
    """
    _register(client)
    with live_app.extensions["gpca_session_factory"]() as session:
        live = session.scalar(
            select(func.count()).select_from(AuthToken).where(AuthToken.consumed_at.is_(None))
        )
    assert live == 1
