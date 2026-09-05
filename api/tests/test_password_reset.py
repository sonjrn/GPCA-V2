"""POST /auth/password/forgot and /auth/password/reset."""

import json
import os
import statistics
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app import create_app
from app.security.passwords import build_hasher, hash_password, verify_password
from app.security.tokens import hash_refresh_token
from app.services import auth as auth_service
from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, RefreshToken, User
from gpca_db.session import build_engine, build_session_factory
from tests.conftest import make_settings

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

PASSWORD = "a-perfectly-good-passphrase"
NEW_PASSWORD = "an-entirely-different-passphrase"
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


def _forgot(client: FlaskClient, email: str = EMAIL):
    return client.post("/api/v1/auth/password/forgot", json={"email": email})


def _reset(client: FlaskClient, token: str, password: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": password}
    )


def _login(client: FlaskClient, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


def _reset_token(app: Flask, user: User) -> str:
    """Mint a token the way the service does, so a test can redeem it.

    The plaintext is only ever returned to the caller of `forgot` -- by email,
    which is not wired up -- so a test that wants one has to issue its own.
    """
    with app.test_request_context(), app.extensions["gpca_session_factory"]() as session:
        attached = session.get(User, user.id)
        assert attached is not None
        token = auth_service.issue_single_use_token(
            session,
            user=attached,
            purpose=AuthTokenPurpose.PASSWORD_RESET,
            ttl=auth_service.PASSWORD_RESET_TTL,
        )
        session.commit()
    return token


def test_forgot_is_202_for_a_known_address(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    assert _forgot(client).status_code == 202


def test_an_unknown_address_gets_the_identical_response(
    live_app: Flask, client: FlaskClient
) -> None:
    """The enumeration defence: same status, same body, no token issued."""
    _make_user(live_app)
    known = _forgot(client)
    unknown = _forgot(client, "nobody@example.org")

    assert known.status_code == unknown.status_code == 202
    assert json.loads(known.data) == json.loads(unknown.data)


def test_no_token_is_created_for_an_unknown_address(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    _forgot(client, "nobody@example.org")
    with live_app.extensions["gpca_session_factory"]() as session:
        rows = session.scalars(
            select(AuthToken).where(AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET)
        ).all()
    assert rows == []


def _median_seconds(call: Callable[[], object], *, samples: int = 5) -> float:
    """Median wall time of `call`, after a discarded warm-up.

    Two things make a single stopwatch reading useless here. The first
    request through a fresh app pays for connection-pool checkout and
    SpecTree's document build, so whichever branch runs first is charged for
    all of it -- which is exactly the branch this test wants to be *slower*,
    so the bias hides a real leak rather than raising a false alarm. And a
    single sample on a shared CI runner is mostly scheduler noise.

    This test failed in CI on precisely that bias: one warm-up-laden reading
    of 88ms against a warm 3ms reading, a 27x ratio from an app that was
    behaving correctly.
    """
    call()
    timings = []
    for _ in range(samples):
        start = time.perf_counter()
        call()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)


def test_the_unknown_path_is_not_measurably_faster(live_app: Flask, client: FlaskClient) -> None:
    """A uniform body is undone by a stopwatch if the branches diverge.

    Unlike login, neither branch runs Argon2 here, so the residual gap is one
    INSERT -- small in absolute terms but a large ratio against work this
    cheap. The margin only needs to catch a miss path that does no work at
    all.
    """
    _make_user(live_app)

    known = _median_seconds(lambda: _forgot(client))
    unknown = _median_seconds(lambda: _forgot(client, "nobody@example.org"))

    assert unknown > known / 8, f"unknown {unknown:.4f}s vs known {known:.4f}s"


def test_a_valid_token_sets_the_new_password(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _reset_token(live_app, user)

    assert _reset(client, token).status_code == 200
    assert _login(client, NEW_PASSWORD).status_code == 200


def test_the_old_password_stops_working(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    _reset(client, _reset_token(live_app, user))
    assert _login(client, PASSWORD).status_code == 401


def test_the_stored_hash_actually_changed(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    before = user.password_hash
    _reset(client, _reset_token(live_app, user))

    with live_app.extensions["gpca_session_factory"]() as session:
        after = session.get(User, user.id).password_hash

    assert after != before
    hasher = build_hasher(memory_cost=19456, time_cost=2, parallelism=1)
    assert verify_password(hasher, after or "", NEW_PASSWORD)


def test_reset_kills_every_existing_session(live_app: Flask, client: FlaskClient) -> None:
    """The whole security purpose of the flow.

    Someone resetting a password usually thinks they are compromised. Leaving
    the attacker's refresh token alive would make the reset theatre.
    """
    user = _make_user(live_app)
    established = json.loads(_login(client).data)

    _reset(client, _reset_token(live_app, user))

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": established["refresh_token"]}
    )
    assert refreshed.status_code == 401


def test_reset_bumps_token_version_so_access_tokens_die(
    live_app: Flask, client: FlaskClient
) -> None:
    """The half that is easy to omit: without it a live access token keeps
    working for its full lifetime after the reset."""
    user = _make_user(live_app)
    access = json.loads(_login(client).data)["access_token"]

    _reset(client, _reset_token(live_app, user))

    with live_app.extensions["gpca_session_factory"]() as session:
        assert session.get(User, user.id).token_version == 1

    response = client.post("/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 401


def test_the_token_is_single_use(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _reset_token(live_app, user)

    assert _reset(client, token).status_code == 200
    assert _reset(client, token, "a-third-distinct-passphrase").status_code == 400


def test_an_expired_token_is_rejected(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _reset_token(live_app, user)

    with live_app.extensions["gpca_session_factory"]() as session:
        row = session.scalars(
            select(AuthToken).where(AuthToken.token_hash == hash_refresh_token(token))
        ).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert _reset(client, token).status_code == 400
    assert _login(client, PASSWORD).status_code == 200


def test_a_verification_token_cannot_reset_a_password(live_app: Flask, client: FlaskClient) -> None:
    """`purpose` is checked rather than inferred from the endpoint.

    Without it, the 24-hour verification link mailed at signup would be a
    24-hour account takeover link.
    """
    user = _make_user(live_app)
    with live_app.test_request_context(), live_app.extensions["gpca_session_factory"]() as session:
        attached = session.get(User, user.id)
        token = auth_service.issue_single_use_token(
            session,
            user=attached,
            purpose=AuthTokenPurpose.EMAIL_VERIFY,
            ttl=auth_service.EMAIL_VERIFY_TTL,
        )
        session.commit()

    assert _reset(client, token).status_code == 400
    assert _login(client, PASSWORD).status_code == 200


def test_a_second_request_invalidates_the_first_token(live_app: Flask, client: FlaskClient) -> None:
    """Enforced by the partial unique index, so every "resend" click does not
    leave another working takeover link in someone's inbox."""
    user = _make_user(live_app)
    first = _reset_token(live_app, user)
    second = _reset_token(live_app, user)

    assert _reset(client, first).status_code == 400
    assert _reset(client, second).status_code == 200


def test_a_reset_token_cannot_verify_an_email(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    token = _reset_token(live_app, user)
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400


def test_an_unknown_token_is_rejected(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    assert _reset(client, "a-token-that-was-never-issued").status_code == 400


def test_a_weak_new_password_is_a_422_naming_the_field(
    live_app: Flask, client: FlaskClient
) -> None:
    user = _make_user(live_app)
    response = _reset(client, _reset_token(live_app, user), "short")

    assert response.status_code == 422
    assert "new_password" in response.data.decode()


def test_a_confirmation_email_is_sent(
    live_app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So an unexpected reset is visible to the person it happened to."""
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(auth_service, "send_email", lambda **kw: sent.append(kw))

    user = _make_user(live_app)
    _reset(client, _reset_token(live_app, user))

    assert [message["template"] for message in sent] == ["password_changed"]


def test_no_email_is_sent_for_a_failed_reset(
    live_app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(auth_service, "send_email", lambda **kw: sent.append(kw))

    _make_user(live_app)
    _reset(client, "a-token-that-was-never-issued")

    assert sent == []


def test_neither_password_is_echoed_or_logged(
    live_app: Flask, client: FlaskClient, capsys: pytest.CaptureFixture[str]
) -> None:
    user = _make_user(live_app)
    response = _reset(client, _reset_token(live_app, user))

    body = response.data.decode()
    logs = capsys.readouterr().out
    for secret in (PASSWORD, NEW_PASSWORD):
        assert secret not in body
        assert secret not in logs


def test_the_endpoints_are_documented(client: FlaskClient) -> None:
    paths = json.loads(client.get("/api/v1/openapi.json").data)["paths"]
    assert "/api/v1/auth/password/forgot" in paths
    assert "/api/v1/auth/password/reset" in paths
