"""GET/PATCH /me and POST /me/password."""

import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.security.passwords import build_hasher, hash_password
from app.services import auth as auth_service
from gpca_db.enums import UserRole, UserStatus
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
        "display_name": "Ada B.",
        "role": UserRole.MEMBER,
        "status": UserStatus.ACTIVE,
        "token_version": 0,
        "email_verified_at": datetime.now(UTC),
        "phone": "+1-555-0100",
        "city": "Poughkeepsie",
        "state_province": "New York",
        "state_code": "NY",
        "country_code": "US",
        "member_since": date(2020, 3, 1),
    }
    defaults.update(fields)
    with app.extensions["gpca_session_factory"]() as session:
        user = User(**defaults)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _tokens(client: FlaskClient, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})
    assert response.status_code == 200
    return json.loads(response.data)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_me(client: FlaskClient, access: str):
    return client.get("/api/v1/me", headers=_auth(access))


def _patch_me(client: FlaskClient, access: str, body: dict[str, object]):
    return client.patch("/api/v1/me", json=body, headers=_auth(access))


def test_get_me_returns_the_current_user(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    response = _get_me(client, _tokens(client)["access_token"])

    assert response.status_code == 200
    body = json.loads(response.data)
    assert body["id"] == str(user.id)
    assert body["email"] == EMAIL
    assert body["role"] == UserRole.MEMBER.value
    assert body["city"] == "Poughkeepsie"
    assert body["member_since"] == "2020-03-01"


def test_get_me_requires_a_token(client: FlaskClient) -> None:
    assert client.get("/api/v1/me").status_code == 401


def test_the_response_leaks_no_internal_columns(live_app: Flask, client: FlaskClient) -> None:
    """Asserted against the raw JSON rather than the model.

    This is the first response built from an ORM row, so it is the first place
    a reach for model_validate(user) would quietly serialize password_hash.
    Checking the class definition would only prove the class is currently
    right; checking the bytes proves what a client actually receives.
    """
    _make_user(live_app)
    response = _get_me(client, _tokens(client)["access_token"])
    body = json.loads(response.data)

    for forbidden in ("password_hash", "token_version", "status", "deleted_at"):
        assert forbidden not in body

    assert "$argon2" not in response.data.decode()


def test_email_verified_is_a_boolean_not_a_timestamp(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    body = json.loads(_get_me(client, _tokens(client)["access_token"]).data)
    assert body["email_verified"] is True
    assert "email_verified_at" not in body


def test_an_unverified_account_reports_false(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app, email_verified_at=None)
    body = json.loads(_get_me(client, _tokens(client)["access_token"]).data)
    assert body["email_verified"] is False


def test_patch_updates_only_what_was_sent(live_app: Flask, client: FlaskClient) -> None:
    """The exclude_unset contract. Without it every omitted field arrives as
    None and a one-field update wipes the rest of the profile."""
    _make_user(live_app)
    access = _tokens(client)["access_token"]

    body = json.loads(_patch_me(client, access, {"city": "Kingston"}).data)

    assert body["city"] == "Kingston"
    assert body["last_name"] == "Byron"
    assert body["phone"] == "+1-555-0100"
    assert body["state_province"] == "New York"


def test_an_explicit_null_clears_a_nullable_field(live_app: Flask, client: FlaskClient) -> None:
    """Distinct from omitting it: one is "remove my phone number", the other
    is "I am not talking about my phone number"."""
    _make_user(live_app)
    access = _tokens(client)["access_token"]

    body = json.loads(_patch_me(client, access, {"phone": None}).data)
    assert body["phone"] is None
    assert body["city"] == "Poughkeepsie"


def test_the_update_is_persisted(live_app: Flask, client: FlaskClient) -> None:
    user = _make_user(live_app)
    access = _tokens(client)["access_token"]
    _patch_me(client, access, {"display_name": "A. Byron"})

    with live_app.extensions["gpca_session_factory"]() as session:
        assert session.get(User, user.id).display_name == "A. Byron"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "admin"),
        ("email", "someone.else@example.org"),
        ("status", "suspended"),
        ("token_version", 99),
        ("member_since", "1999-01-01"),
    ],
)
def test_privileged_fields_are_a_422_not_a_silent_ignore(
    live_app: Flask, client: FlaskClient, field: str, value: object
) -> None:
    """extra="forbid" rather than a filter someone has to remember.

    A silent ignore is the worse failure: the client believes it promoted
    itself and the server never says otherwise.
    """
    user = _make_user(live_app)
    access = _tokens(client)["access_token"]

    assert _patch_me(client, access, {field: value}).status_code == 422

    with live_app.extensions["gpca_session_factory"]() as session:
        unchanged = session.get(User, user.id)
        assert unchanged is not None
        assert unchanged.role is UserRole.MEMBER
        assert unchanged.email == EMAIL
        assert unchanged.status is UserStatus.ACTIVE


def test_clearing_a_required_name_is_a_422(live_app: Flask, client: FlaskClient) -> None:
    """NOT NULL in the database, so this has to fail at the wire model rather
    than as an IntegrityError from the flush."""
    _make_user(live_app)
    access = _tokens(client)["access_token"]

    response = _patch_me(client, access, {"first_name": None})
    assert response.status_code == 422
    assert "first_name" in response.data.decode()


def test_an_empty_name_is_a_422(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    access = _tokens(client)["access_token"]
    assert _patch_me(client, access, {"first_name": "   "}).status_code == 422


def test_country_and_state_codes_are_normalized(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    access = _tokens(client)["access_token"]

    body = json.loads(_patch_me(client, access, {"country_code": "ca", "state_code": "on"}).data)
    assert body["country_code"] == "CA"
    assert body["state_code"] == "ON"


def test_patch_requires_a_token(client: FlaskClient) -> None:
    assert client.patch("/api/v1/me", json={"city": "Kingston"}).status_code == 401


def _change_password(client: FlaskClient, access: str, **body: str):
    payload = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    payload.update(body)
    return client.post("/api/v1/me/password", json=payload, headers=_auth(access))


def test_a_password_change_returns_a_working_pair(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    access = _tokens(client)["access_token"]

    response = _change_password(client, access)
    assert response.status_code == 200

    issued = json.loads(response.data)
    assert _get_me(client, issued["access_token"]).status_code == 200


def test_the_new_password_works_and_the_old_does_not(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    _change_password(client, _tokens(client)["access_token"])

    good = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD})
    stale = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert good.status_code == 200
    assert stale.status_code == 401


def test_other_sessions_die_but_the_callers_survives(live_app: Flask, client: FlaskClient) -> None:
    """The point of returning a pair rather than a 204.

    Every session must die -- that is what a password change is for -- but the
    person doing it should not be signed out of the browser they are sitting
    in, so they are handed a replacement.
    """
    _make_user(live_app)
    elsewhere = _tokens(client)
    here = _tokens(client)

    issued = json.loads(_change_password(client, here["access_token"]).data)

    # The other device is gone, refresh and access alike.
    stale_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": elsewhere["refresh_token"]}
    )
    assert stale_refresh.status_code == 401
    assert _get_me(client, elsewhere["access_token"]).status_code == 401

    # The caller keeps working on the pair they were just handed.
    assert _get_me(client, issued["access_token"]).status_code == 200
    fresh_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": issued["refresh_token"]}
    )
    assert fresh_refresh.status_code == 200


def test_the_callers_old_access_token_is_also_dead(live_app: Flask, client: FlaskClient) -> None:
    """token_version does not make exceptions, which is why a replacement pair
    is returned rather than being optional."""
    _make_user(live_app)
    access = _tokens(client)["access_token"]
    _change_password(client, access)
    assert _get_me(client, access).status_code == 401


def test_a_wrong_current_password_changes_nothing(live_app: Flask, client: FlaskClient) -> None:
    """Without this check, fifteen minutes of borrowed access token becomes a
    password the real owner does not know."""
    _make_user(live_app)
    session_tokens = _tokens(client)

    response = _change_password(
        client, session_tokens["access_token"], current_password="not-the-right-password"
    )
    assert response.status_code == 401

    # Nothing moved: the old password still works and the session is intact.
    login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    still_live = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session_tokens["refresh_token"]}
    )
    assert still_live.status_code == 200


def test_a_weak_new_password_is_a_422_naming_the_field(
    live_app: Flask, client: FlaskClient
) -> None:
    _make_user(live_app)
    response = _change_password(client, _tokens(client)["access_token"], new_password="short")

    assert response.status_code == 422
    assert "new_password" in response.data.decode()


def test_a_notification_email_is_sent(
    live_app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(auth_service, "send_email", lambda **kw: sent.append(kw))

    _make_user(live_app)
    _change_password(client, _tokens(client)["access_token"])

    assert [message["template"] for message in sent] == ["password_changed"]


def test_no_notification_for_a_rejected_change(
    live_app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(auth_service, "send_email", lambda **kw: sent.append(kw))

    _make_user(live_app)
    _change_password(
        client, _tokens(client)["access_token"], current_password="not-the-right-password"
    )
    assert sent == []


def test_the_password_change_requires_a_token(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 401


def test_no_password_is_echoed(live_app: Flask, client: FlaskClient) -> None:
    _make_user(live_app)
    body = _change_password(client, _tokens(client)["access_token"]).data.decode()
    assert PASSWORD not in body
    assert NEW_PASSWORD not in body


def test_the_endpoints_are_documented(client: FlaskClient) -> None:
    document = json.loads(client.get("/api/v1/openapi.json").data)
    assert "/api/v1/me" in document["paths"]
    assert "/api/v1/me/password" in document["paths"]

    schemas = document["components"]["schemas"]
    assert "MeResponse" in schemas
    assert "MeUpdate" in schemas
    assert "ChangePasswordRequest" in schemas
