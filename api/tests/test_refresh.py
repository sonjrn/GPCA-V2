"""POST /auth/refresh, /auth/logout and /auth/logout-all.

The reuse-detection tests are the reason refresh tokens are database rows
rather than self-contained JWTs, so they are the ones worth reading.
"""

import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app import create_app
from app.security.passwords import build_hasher, hash_password
from app.security.tokens import decode_access_token, hash_refresh_token
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


def _login(client: FlaskClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    return json.loads(response.data)


def _refresh(client: FlaskClient, token: str):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": token})


def _logout(client: FlaskClient, token: str):
    return client.post("/api/v1/auth/logout", json={"refresh_token": token})


def _row(app: Flask, token: str) -> RefreshToken:
    with app.extensions["gpca_session_factory"]() as session:
        return session.scalars(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
        ).one()


def test_refresh_returns_a_new_pair(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    first = _login(client)

    response = _refresh(client, first["refresh_token"])
    assert response.status_code == 200

    body = json.loads(response.data)
    assert body["refresh_token"] != first["refresh_token"]
    claims = decode_access_token(secret=TEST_SECRET, token=body["access_token"])
    assert claims.user_id == user.id


def test_the_presented_token_stops_working(live_app: Flask, client: FlaskClient) -> None:
    """Single use. Without this, a stolen token is good until it expires."""
    _make_user(live_app)
    original = _login(client)["refresh_token"]

    assert _refresh(client, original).status_code == 200
    assert _refresh(client, original).status_code == 401


def test_the_replacement_stays_in_the_family_and_is_linked(
    live_app: Flask, client: FlaskClient
) -> None:
    _make_user(live_app)
    original = _login(client)["refresh_token"]
    replacement = json.loads(_refresh(client, original).data)["refresh_token"]

    old, new = _row(live_app, original), _row(live_app, replacement)
    assert new.family_id == old.family_id
    assert old.replaced_by_id == new.id
    assert old.revoked_at is not None


def test_reuse_revokes_the_whole_family(live_app: Flask, client: FlaskClient) -> None:
    """The heart of the design.

    A replayed token means the chain is compromised, so the still-valid newest
    descendant has to die too -- otherwise whoever holds it keeps the session.
    """
    _make_user(live_app)
    original = _login(client)["refresh_token"]
    current = json.loads(_refresh(client, original).data)["refresh_token"]

    # Replay the consumed token.
    assert _refresh(client, original).status_code == 401

    # The token that was valid a moment ago is now dead as well.
    assert _refresh(client, current).status_code == 401
    assert _row(live_app, current).revoked_at is not None


def test_another_family_survives_a_revocation(live_app: Flask, client: FlaskClient) -> None:
    """Blast radius. A compromised chain must not sign the user out of a
    laptop that had nothing to do with it."""
    _make_user(live_app)
    compromised = _login(client)["refresh_token"]
    untouched = _login(client)["refresh_token"]

    _refresh(client, compromised)
    _refresh(client, compromised)  # triggers family revocation

    assert _refresh(client, untouched).status_code == 200


def test_an_expired_token_is_rejected_without_revoking_the_family(
    live_app: Flask, client: FlaskClient
) -> None:
    """Expiry is ordinary, not an attack, so it must not nuke the lineage."""
    _make_user(live_app)
    original = _login(client)["refresh_token"]
    current = json.loads(_refresh(client, original).data)["refresh_token"]

    with live_app.extensions["gpca_session_factory"]() as session:
        row = session.scalars(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(current))
        ).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        family_id = row.family_id
        session.commit()

    assert _refresh(client, current).status_code == 401

    # Nothing in the lineage was revoked in response: the only revoked row is
    # the one rotation already consumed.
    with live_app.extensions["gpca_session_factory"]() as session:
        rows = session.scalars(
            select(RefreshToken).where(RefreshToken.family_id == family_id)
        ).all()
    assert sum(row.revoked_at is not None for row in rows) == 1
    assert _row(live_app, current).revoked_at is None


def test_an_unknown_token_is_rejected_the_same_way(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    consumed = _login(client)["refresh_token"]
    _refresh(client, consumed)

    unknown = _refresh(client, "a-token-that-was-never-issued")
    replayed = _refresh(client, consumed)

    assert unknown.status_code == replayed.status_code == 401
    assert json.loads(unknown.data)["detail"] == json.loads(replayed.data)["detail"]


class _Capture(logging.Handler):
    """caplog cannot see these records.

    `configure_logging` clears the root handlers when the app is built, which
    removes pytest's, and the test settings pin the root level to CRITICAL --
    so a warning is dropped before any handler runs. Attaching directly to the
    service logger tests what the code emits rather than how it is wired up.
    """

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_reuse_is_logged_with_the_family_id(live_app: Flask, client: FlaskClient) -> None:
    """One of the few signals of a genuinely stolen session, so it has to be
    findable in the logs rather than only visible as a 401."""
    _make_user(live_app)
    original = _login(client)["refresh_token"]
    family_id = _row(live_app, original).family_id
    _refresh(client, original)

    logger = logging.getLogger("app.services.auth")
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        _refresh(client, original)
    finally:
        logger.setLevel(previous)
        logger.removeHandler(handler)

    record = next(r for r in handler.records if r.levelno == logging.WARNING)
    assert record.family_id == str(family_id)
    assert record.user_id


def test_a_suspended_user_cannot_refresh(live_app: Flask, client: FlaskClient) -> None:
    """A suspension has to bite before the next login, not after."""
    user = _make_user(live_app)
    token = _login(client)["refresh_token"]

    with live_app.extensions["gpca_session_factory"]() as session:
        session.get(User, user.id).status = UserStatus.SUSPENDED
        session.commit()

    assert _refresh(client, token).status_code == 401


def test_logout_revokes_only_the_presented_token(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    phone = _login(client)["refresh_token"]
    laptop = _login(client)["refresh_token"]

    assert _logout(client, phone).status_code == 204
    assert _refresh(client, phone).status_code == 401
    assert _refresh(client, laptop).status_code == 200


def test_logout_twice_is_not_an_error(live_app: Flask, client: FlaskClient) -> None:
    """A 4xx on the second call tells an attacker their token was burned, and
    punishes a client that is merely tidying up."""
    _make_user(live_app)
    token = _login(client)["refresh_token"]

    assert _logout(client, token).status_code == 204
    assert _logout(client, token).status_code == 204


def test_logout_on_an_unknown_token_succeeds(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    assert _logout(client, "a-token-that-was-never-issued").status_code == 204


def test_logout_does_not_trigger_family_revocation(live_app: Flask, client: FlaskClient) -> None:
    """Logging out is not reuse: the rest of the lineage is already dead by
    rotation, but a deliberate sign-out must not be logged as an attack."""
    _make_user(live_app)
    original = _login(client)["refresh_token"]
    current = json.loads(_refresh(client, original).data)["refresh_token"]

    assert _logout(client, original).status_code == 204
    assert _row(live_app, current).revoked_at is None


def test_logout_all_kills_every_family(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    phone = _login(client)
    laptop = _login(client)

    response = client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {phone['access_token']}"},
    )
    assert response.status_code == 204

    assert _refresh(client, phone["refresh_token"]).status_code == 401
    assert _refresh(client, laptop["refresh_token"]).status_code == 401


def test_logout_all_invalidates_live_access_tokens(live_app: Flask, client: FlaskClient) -> None:
    """The subtle half. Revoking refresh tokens alone leaves an access token
    working for its full lifetime, so "everywhere" would be a lie."""
    user = _make_user(live_app)
    access = _login(client)["access_token"]

    client.post("/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {access}"})

    with live_app.extensions["gpca_session_factory"]() as session:
        assert session.get(User, user.id).token_version == 1

    # The same token that authorized the logout no longer authorizes anything.
    replay = client.post("/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {access}"})
    assert replay.status_code == 401


def test_logout_all_requires_authentication(client: FlaskClient) -> None:
    assert client.post("/api/v1/auth/logout-all").status_code == 401


def test_the_endpoints_are_documented(client: FlaskClient) -> None:
    paths = json.loads(client.get("/api/v1/openapi.json").data)["paths"]
    for path in ("/api/v1/auth/refresh", "/api/v1/auth/logout", "/api/v1/auth/logout-all"):
        assert path in paths
