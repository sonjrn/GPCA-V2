"""POST /auth/register."""

import json
import os
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select

from app import create_app
from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, User
from gpca_db.session import build_engine, build_session_factory
from tests.conftest import make_settings

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

VALID = {
    "email": "ada@example.org",
    "password": "a-perfectly-good-passphrase",
    "first_name": "Ada",
    "last_name": "Byron",
}


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


def _session(app: Flask):
    return app.extensions["gpca_session_factory"]()


def _post(client: FlaskClient, **overrides: str):
    return client.post("/api/v1/auth/register", json={**VALID, **overrides})


def test_a_new_address_creates_an_unverified_viewer(live_app: Flask, client: FlaskClient) -> None:
    assert _post(client).status_code == 202

    with _session(live_app) as session:
        user = session.scalars(select(User)).one()
    assert user.email == "ada@example.org"
    assert user.role is UserRole.VIEWER
    assert user.status is UserStatus.ACTIVE
    assert user.email_verified_at is None
    assert user.token_version == 0


def test_the_response_reveals_nothing(client: FlaskClient) -> None:
    body = json.loads(_post(client).data)
    assert set(body) == {"detail"}
    assert "id" not in body
    assert "token" not in body


def test_a_duplicate_address_is_byte_identical(live_app: Flask, client: FlaskClient) -> None:
    """The enumeration defence. A 409 here would list the club roster."""
    first = _post(client)
    second = _post(client)
    assert first.status_code == second.status_code == 202
    assert first.data == second.data

    with _session(live_app) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_the_duplicate_path_is_not_measurably_faster(client: FlaskClient) -> None:
    """Skipping the hash on the duplicate branch would leak by stopwatch."""
    start = time.perf_counter()
    _post(client)
    fresh = time.perf_counter() - start

    start = time.perf_counter()
    _post(client)
    duplicate = time.perf_counter() - start

    assert duplicate > fresh / 4, f"duplicate {duplicate:.3f}s vs fresh {fresh:.3f}s"


def test_email_matching_is_case_insensitive(live_app: Flask, client: FlaskClient) -> None:
    _post(client, email="ada@example.org")
    _post(client, email="ADA@example.org")
    with _session(live_app) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_a_verification_token_is_issued(live_app: Flask, client: FlaskClient) -> None:
    _post(client)
    with _session(live_app) as session:
        token = session.scalars(select(AuthToken)).one()
    assert token.purpose is AuthTokenPurpose.EMAIL_VERIFY
    assert token.consumed_at is None
    assert len(token.token_hash) == 32


@pytest.mark.parametrize(
    ("field", "value"),
    [("password", "short"), ("password", ""), ("first_name", ""), ("email", "not-an-email")],
)
def test_invalid_input_is_422_naming_the_field(client: FlaskClient, field: str, value: str) -> None:
    response = _post(client, **{field: value})
    assert response.status_code == 422
    assert response.mimetype == "application/problem+json"
    assert any(field in error["field"] for error in json.loads(response.data)["errors"])


def test_a_long_passphrase_is_accepted(live_app: Flask, client: FlaskClient) -> None:
    """Argon2 has no 72-byte truncation, so there is no reason to cap this."""
    assert _post(client, password="correct horse battery staple " * 5).status_code == 202
    with _session(live_app) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_privilege_fields_are_rejected_not_ignored(client: FlaskClient) -> None:
    """extra="forbid": sending role must fail, not be silently dropped."""
    response = client.post("/api/v1/auth/register", json={**VALID, "role": "admin"})
    assert response.status_code == 422
    assert any("role" in error["field"] for error in json.loads(response.data)["errors"])


def test_a_failing_email_send_does_not_roll_back_the_account(
    live_app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Email is best effort; the account is the thing that must persist."""
    import app.integrations.email as email_module

    def explode(**_: object) -> None:
        raise RuntimeError("SES is down")

    monkeypatch.setattr(email_module, "send_email", explode)
    monkeypatch.setattr("app.services.auth.send_email", explode)

    assert _post(client).status_code == 202
    with _session(live_app) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_the_password_is_not_echoed_or_stored_in_clear(
    live_app: Flask, client: FlaskClient
) -> None:
    response = _post(client)
    assert VALID["password"] not in response.data.decode()
    with _session(live_app) as session:
        user = session.scalars(select(User)).one()
    assert user.password_hash is not None
    assert VALID["password"] not in user.password_hash


def test_the_endpoint_is_documented(client: FlaskClient) -> None:
    document = json.loads(client.get("/api/v1/openapi.json").data)
    assert "/api/v1/auth/register" in document["paths"]
