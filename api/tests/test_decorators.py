"""Route guards, against a real database.

These need PostgreSQL because the guards deliberately read the user row on
every request -- that read is what makes a role change or a suspension take
effect immediately rather than when the access token happens to expire.
"""

import json
import os
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.security.decorators import (
    current_user,
    require_admin,
    require_auth,
    require_role,
    require_verified_email,
)
from app.security.tokens import issue_access_token
from gpca_db.enums import UserRole, UserStatus
from gpca_db.models import User
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
        session.query(User).delete()
        session.commit()
    yield app
    engine.dispose()


@pytest.fixture
def guarded_client(live_app: Flask) -> FlaskClient:
    @live_app.get("/probe/any")
    @require_auth
    def _any() -> dict[str, str]:
        return {"email": current_user().email}

    @live_app.get("/probe/member")
    @require_role(UserRole.MEMBER)
    def _member() -> dict[str, str]:
        return {"ok": "member"}

    @live_app.get("/probe/admin")
    @require_admin
    def _admin() -> dict[str, str]:
        return {"ok": "admin"}

    @live_app.get("/probe/verified")
    @require_verified_email
    def _verified() -> dict[str, str]:
        return {"ok": "verified"}

    return live_app.test_client()


def _make_user(app: Flask, **fields: object) -> User:
    factory = app.extensions["gpca_session_factory"]
    defaults: dict[str, object] = {
        "email": "ada@example.org",
        "password_hash": "x",
        "first_name": "Ada",
        "last_name": "Byron",
        "role": UserRole.VIEWER,
        "status": UserStatus.ACTIVE,
        "token_version": 0,
    }
    defaults.update(fields)
    with factory() as session:
        user = User(**defaults)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _auth(user: User, *, token_version: int | None = None) -> dict[str, str]:
    token, _ = issue_access_token(
        secret=TEST_SECRET,
        user_id=user.id,
        role=user.role.value,
        token_version=user.token_version if token_version is None else token_version,
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_valid_token_identifies_the_caller(live_app: Flask, guarded_client: FlaskClient) -> None:
    user = _make_user(live_app)
    response = guarded_client.get("/probe/any", headers=_auth(user))
    assert response.status_code == 200
    assert json.loads(response.data)["email"] == "ada@example.org"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}],
    ids=["no header", "malformed token", "wrong scheme"],
)
def test_bad_credentials_are_401_problem_json(
    guarded_client: FlaskClient, headers: dict[str, str]
) -> None:
    response = guarded_client.get("/probe/any", headers=headers)
    assert response.status_code == 401
    assert response.mimetype == "application/problem+json"


def test_an_expired_token_is_rejected(live_app: Flask, guarded_client: FlaskClient) -> None:
    user = _make_user(live_app)
    token, _ = issue_access_token(
        secret=TEST_SECRET,
        user_id=user.id,
        role=user.role.value,
        token_version=0,
        ttl_seconds=-1,
    )
    response = guarded_client.get("/probe/any", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_role_check_is_ordered_not_set_membership(
    live_app: Flask, guarded_client: FlaskClient
) -> None:
    """An admin satisfies a member requirement without being listed."""
    admin = _make_user(live_app, email="admin@example.org", role=UserRole.ADMIN)
    assert guarded_client.get("/probe/member", headers=_auth(admin)).status_code == 200


def test_a_viewer_is_forbidden_from_member_routes(
    live_app: Flask, guarded_client: FlaskClient
) -> None:
    viewer = _make_user(live_app)
    response = guarded_client.get("/probe/member", headers=_auth(viewer))
    assert response.status_code == 403
    assert response.mimetype == "application/problem+json"


def test_a_member_is_forbidden_from_admin_routes(
    live_app: Flask, guarded_client: FlaskClient
) -> None:
    member = _make_user(live_app, role=UserRole.MEMBER)
    assert guarded_client.get("/probe/admin", headers=_auth(member)).status_code == 403


def test_bumping_token_version_invalidates_a_live_token(
    live_app: Flask, guarded_client: FlaskClient
) -> None:
    """The whole reason the guard reads the database.

    A role change or logout-everywhere must take effect now, not in fifteen
    minutes when the access token happens to expire.
    """
    user = _make_user(live_app)
    headers = _auth(user)
    assert guarded_client.get("/probe/any", headers=headers).status_code == 200

    factory = live_app.extensions["gpca_session_factory"]
    with factory() as session:
        session.get(User, user.id).token_version = 1
        session.commit()

    assert guarded_client.get("/probe/any", headers=headers).status_code == 401


def test_a_suspended_account_is_rejected(live_app: Flask, guarded_client: FlaskClient) -> None:
    user = _make_user(live_app, status=UserStatus.SUSPENDED)
    assert guarded_client.get("/probe/any", headers=_auth(user)).status_code == 401


def test_a_deleted_account_is_rejected(live_app: Flask, guarded_client: FlaskClient) -> None:
    from datetime import UTC, datetime

    user = _make_user(live_app, deleted_at=datetime.now(UTC))
    assert guarded_client.get("/probe/any", headers=_auth(user)).status_code == 401


def test_verified_email_gate(live_app: Flask, guarded_client: FlaskClient) -> None:
    from datetime import UTC, datetime

    unverified = _make_user(live_app)
    assert guarded_client.get("/probe/verified", headers=_auth(unverified)).status_code == 403

    verified = _make_user(live_app, email="v@example.org", email_verified_at=datetime.now(UTC))
    assert guarded_client.get("/probe/verified", headers=_auth(verified)).status_code == 200
