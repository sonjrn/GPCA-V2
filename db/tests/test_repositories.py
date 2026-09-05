"""Repository queries against a real PostgreSQL.

These are the reason the repositories exist as a layer: the queries are
testable on their own, without a Flask app or a request context, and the
partial-index behaviour they depend on is real rather than mocked.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, User
from gpca_db.repositories import auth_tokens as auth_token_repo
from gpca_db.repositories import users as user_repo
from gpca_db.session import build_engine, build_session_factory, is_reachable

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; repository tests need PostgreSQL"
)

MIGRATIONS_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def _schema() -> None:
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "migrations"))
    command.upgrade(config, "head")


@pytest.fixture
def session(_schema: None) -> Iterator[Session]:
    """A session whose work is rolled back, so tests do not see each other."""
    engine = build_engine(DATABASE_URL or "")
    factory = build_session_factory(engine)
    with factory() as session:
        session.query(AuthToken).delete()
        session.query(User).delete()
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


def _user(**fields: object) -> User:
    defaults: dict[str, object] = {
        "email": "ada@example.org",
        "password_hash": "not-a-real-hash",
        "first_name": "Ada",
        "last_name": "Byron",
        "role": UserRole.VIEWER,
        "status": UserStatus.ACTIVE,
        "token_version": 0,
    }
    defaults.update(fields)
    return User(**defaults)


def test_add_flushes_so_the_id_is_readable(session: Session) -> None:
    """Services build related rows in the same unit of work, which needs the
    id before commit. The column default only runs during the INSERT, so
    without the flush inside `add` this is None."""
    assert user_repo.add(session, _user()).id is not None


def test_add_flushes_so_a_child_row_can_reference_it(session: Session) -> None:
    """The reason `add` flushes, and a guard against removing it.

    SQLAlchemy orders pending inserts by ORM `relationship()`. This schema
    declares none, so a raw ForeignKey column tells the unit of work nothing
    and it will INSERT a child before its parent. Without the flush inside
    `add`, the token below fails on fk_auth_tokens_user_id_users.
    """
    user = user_repo.add(session, _user())
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"p" * 32))
    session.flush()

    assert auth_token_repo.get_by_hash(session, b"p" * 32) is not None


def test_get_by_email_finds_a_live_account(session: Session) -> None:
    user = user_repo.add(session, _user())
    assert user_repo.get_by_email(session, "ada@example.org") is user


def test_get_by_email_is_case_insensitive(session: Session) -> None:
    """citext, so a lookup matches however the address was typed."""
    user_repo.add(session, _user())
    assert user_repo.get_by_email(session, "ADA@Example.ORG") is not None


def test_get_by_email_ignores_a_soft_deleted_account(session: Session) -> None:
    """The address is released when the account is removed -- which is why
    uq_users_email is partial."""
    user_repo.add(session, _user(deleted_at=datetime.now(UTC)))
    assert user_repo.get_by_email(session, "ada@example.org") is None


def test_get_by_email_returns_none_for_an_unknown_address(session: Session) -> None:
    assert user_repo.get_by_email(session, "nobody@example.org") is None


def test_get_returns_a_soft_deleted_account(session: Session) -> None:
    """Unlike get_by_email. A token outliving its account is a distinct
    condition from a token that was never valid, so the guard needs to see the
    row to tell them apart."""
    user = user_repo.add(session, _user(deleted_at=datetime.now(UTC)))
    assert user_repo.get(session, user.id) is user


def _token(user: User, purpose: AuthTokenPurpose, digest: bytes) -> AuthToken:
    return AuthToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=digest,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_get_by_hash_finds_a_token(session: Session) -> None:
    user = user_repo.add(session, _user())
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"a" * 32))
    session.flush()

    found = auth_token_repo.get_by_hash(session, b"a" * 32)
    assert found is not None
    assert found.purpose is AuthTokenPurpose.EMAIL_VERIFY


def test_get_by_hash_does_not_filter_by_purpose_or_state(session: Session) -> None:
    """Deliberate: the caller distinguishes expired from consumed from
    wrong-purpose, because those are one response but different log lines."""
    user = user_repo.add(session, _user())
    row = _token(user, AuthTokenPurpose.PASSWORD_RESET, b"b" * 32)
    row.consumed_at = datetime.now(UTC)
    auth_token_repo.add(session, row)
    session.flush()

    assert auth_token_repo.get_by_hash(session, b"b" * 32) is not None


def test_get_by_hash_returns_none_for_an_unknown_digest(session: Session) -> None:
    assert auth_token_repo.get_by_hash(session, b"z" * 32) is None


def test_consume_outstanding_marks_live_tokens(session: Session) -> None:
    user = user_repo.add(session, _user())
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"c" * 32))
    session.flush()

    now = datetime.now(UTC)
    consumed = auth_token_repo.consume_outstanding(
        session, user_id=user.id, purpose=AuthTokenPurpose.EMAIL_VERIFY, now=now
    )

    assert consumed == 1
    row = auth_token_repo.get_by_hash(session, b"c" * 32)
    assert row is not None
    assert row.consumed_at == now


def test_consume_outstanding_leaves_other_purposes_alone(session: Session) -> None:
    """A resend of a verification link must not invalidate a reset link."""
    user = user_repo.add(session, _user())
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"d" * 32))
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.PASSWORD_RESET, b"e" * 32))
    session.flush()

    auth_token_repo.consume_outstanding(
        session,
        user_id=user.id,
        purpose=AuthTokenPurpose.EMAIL_VERIFY,
        now=datetime.now(UTC),
    )

    reset = auth_token_repo.get_by_hash(session, b"e" * 32)
    assert reset is not None
    assert reset.consumed_at is None


def test_consume_outstanding_is_what_makes_reissuing_legal(session: Session) -> None:
    """The partial unique index permits one live row per (user, purpose), so
    without this call a second token would raise IntegrityError."""
    user = user_repo.add(session, _user())
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"f" * 32))
    session.flush()

    auth_token_repo.consume_outstanding(
        session,
        user_id=user.id,
        purpose=AuthTokenPurpose.EMAIL_VERIFY,
        now=datetime.now(UTC),
    )
    auth_token_repo.add(session, _token(user, AuthTokenPurpose.EMAIL_VERIFY, b"g" * 32))
    session.flush()

    assert auth_token_repo.get_by_hash(session, b"g" * 32) is not None


def test_consume_outstanding_on_nothing_is_zero(session: Session) -> None:
    user = user_repo.add(session, _user())
    assert (
        auth_token_repo.consume_outstanding(
            session,
            user_id=user.id,
            purpose=AuthTokenPurpose.EMAIL_VERIFY,
            now=datetime.now(UTC),
        )
        == 0
    )


def test_is_reachable_against_a_live_database() -> None:
    engine = build_engine(DATABASE_URL or "")
    try:
        assert is_reachable(engine) is True
    finally:
        engine.dispose()


def test_is_reachable_is_false_when_nothing_is_listening() -> None:
    """The readiness probe reports a boolean; an unreachable database is the
    answer it exists to give, not an exception."""
    engine = build_engine(
        "postgresql+psycopg://user@127.0.0.1:1/nonexistent", connect_timeout_seconds=1
    )
    try:
        assert is_reachable(engine) is False
    finally:
        engine.dispose()
