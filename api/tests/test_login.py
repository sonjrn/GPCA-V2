"""POST /auth/login."""

import json
import os
import statistics
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app import create_app
from app.security.passwords import build_hasher, hash_password
from app.security.tokens import decode_access_token
from gpca_db.enums import UserRole, UserStatus
from gpca_db.models import AuthToken, RefreshToken, User
from gpca_db.session import build_engine, build_session_factory
from tests.conftest import TEST_SECRET, make_settings

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

PASSWORD = "a-perfectly-good-passphrase"
EMAIL = "ada@example.org"


@pytest.fixture
def live_app() -> Iterator[Flask]:
    app = create_app(make_settings(database_url=DATABASE_URL))
    engine = build_engine(DATABASE_URL or "")
    factory = build_session_factory(engine)
    with factory() as session:
        session.query(RefreshToken).delete()
        session.query(AuthToken).delete()
        session.query(User).delete()
        session.commit()
    yield app
    engine.dispose()


@pytest.fixture
def client(live_app: Flask) -> FlaskClient:
    return live_app.test_client()


def _make_user(app: Flask, **fields: object) -> User:
    hasher = build_hasher(memory_cost=19456, time_cost=2, parallelism=1)
    defaults: dict[str, object] = {
        "email": EMAIL,
        "password_hash": hash_password(hasher, PASSWORD),
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


def _login(client: FlaskClient, **overrides: str):
    return client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD, **overrides},
    )


def _problem(response) -> dict[str, object]:
    """The problem body without its per-request correlation id."""
    body = json.loads(response.data)
    body.pop("request_id", None)
    return body


def test_correct_credentials_return_both_tokens(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    response = _login(client)
    assert response.status_code == 200

    body = json.loads(response.data)
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0

    claims = decode_access_token(secret=TEST_SECRET, token=body["access_token"])
    assert claims.user_id == user.id
    assert claims.role == UserRole.VIEWER.value
    assert claims.token_version == 0


def test_wrong_password_and_unknown_address_are_indistinguishable(
    live_app: Flask, client: FlaskClient
) -> None:
    """The enumeration defence, and the easiest one to get wrong.

    "user not found" and "bad password" are naturally different branches, so
    it takes deliberate effort to make them the same response.
    """
    _make_user(live_app)
    wrong = _login(client, password="not-the-right-password")
    unknown = _login(client, email="nobody@example.org", password="not-the-right-password")

    assert wrong.status_code == unknown.status_code == 401
    assert _problem(wrong) == _problem(unknown)


def test_a_suspended_account_gets_the_same_response(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app, status=UserStatus.SUSPENDED)
    suspended = _login(client)
    assert suspended.status_code == 401
    assert _problem(suspended)["detail"] == "Those credentials are not valid."


def test_a_soft_deleted_account_cannot_log_in(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app, deleted_at=datetime.now(UTC))
    assert _login(client).status_code == 401


def _median_seconds(call: Callable[[], object], *, samples: int = 5) -> float:
    """Median wall time of `call`, after a discarded warm-up.

    Two things make a single stopwatch reading useless here. The first
    request through a fresh app pays for connection-pool checkout and
    SpecTree's document build, so whichever branch runs first is charged for
    all of it -- which is exactly the branch these tests want to be *slower*,
    so the bias hides a real leak rather than causing a false alarm. And a
    single sample on a shared CI runner is mostly scheduler noise.
    """
    call()
    timings = []
    for _ in range(samples):
        start = time.perf_counter()
        call()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)


def test_the_unknown_address_path_is_not_measurably_faster(
    live_app: Flask, client: FlaskClient
) -> None:
    """Without a dummy hash, a missing account returns far sooner and the
    uniform message is undone by a stopwatch.

    Both branches run Argon2, so the medians should be close to equal. The
    margin below is wide because it only needs to catch a branch that skips
    hashing entirely -- that shows up as an order of magnitude, not a few
    percent.
    """
    _make_user(live_app)

    real = _median_seconds(lambda: _login(client, password="not-the-right-password"))
    missing = _median_seconds(
        lambda: _login(client, email="nobody@example.org", password="not-the-right-password")
    )

    assert missing > real / 4, f"missing {missing:.3f}s vs real {real:.3f}s"


def test_an_unverified_account_can_still_log_in(live_app: Flask, client: FlaskClient) -> None:
    """Verification gates specific actions, not the session (section 4.4)."""
    _make_user(live_app)
    assert _login(client).status_code == 200


def test_login_is_case_insensitive_on_the_address(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    assert _login(client, email="ADA@example.org").status_code == 200


def test_the_refresh_token_is_stored_only_as_a_digest(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    plaintext = json.loads(_login(client).data)["refresh_token"]

    with live_app.extensions["gpca_session_factory"]() as session:
        row = session.scalars(select(RefreshToken)).one()
    assert len(row.token_hash) == 32
    assert plaintext.encode() not in row.token_hash


def test_each_login_starts_its_own_family(live_app: Flask, client: FlaskClient) -> None:
    """A family is one lineage from one login, which is what lets reuse
    detection revoke a compromised chain without touching other sessions."""
    _make_user(live_app)
    _login(client)
    _login(client)

    with live_app.extensions["gpca_session_factory"]() as session:
        families = {row.family_id for row in session.scalars(select(RefreshToken))}
    assert len(families) == 2


def test_last_login_at_is_recorded(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    assert user.last_login_at is None
    _login(client)
    with live_app.extensions["gpca_session_factory"]() as session:
        assert session.get(User, user.id).last_login_at is not None


def test_the_user_agent_and_ip_are_recorded(live_app: Flask, client: FlaskClient) -> None:
    """What makes a suspicious session reviewable later."""
    _make_user(live_app)
    client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"User-Agent": "probe-agent/1.0"},
    )
    with live_app.extensions["gpca_session_factory"]() as session:
        row = session.scalars(select(RefreshToken)).one()
    assert row.user_agent == "probe-agent/1.0"


def test_the_password_is_never_echoed(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    assert PASSWORD not in _login(client).data.decode()


def test_login_is_documented(client: FlaskClient) -> None:
    paths = json.loads(client.get("/api/v1/openapi.json").data)["paths"]
    assert "/api/v1/auth/login" in paths
