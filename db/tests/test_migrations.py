"""Migration tests. These need a real PostgreSQL and are skipped without one.

Set DATABASE_URL to run them; CI (#7) provides a service container. They build
the schema by running Alembic rather than create_all(), so the migrations
themselves are exercised, not just the models.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from gpca_db.session import build_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; migration tests need PostgreSQL"
)

MIGRATIONS_ROOT = Path(__file__).parent.parent


def _alembic_config() -> Config:
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "migrations"))
    return config


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """An engine against a database migrated to head, torn down afterwards."""
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = build_engine(DATABASE_URL or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def test_single_head() -> None:
    """More than one head means someone's merge forked the migration history."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, f"expected one head, found {heads}"


def test_round_trip_leaves_nothing_behind(migrated_engine: Engine) -> None:
    """downgrade base must undo everything upgrade head created.

    A downgrade that leaves an enum type behind fails the *next* upgrade with
    'type already exists', which is a confusing way to discover it.
    """
    config = _alembic_config()
    command.downgrade(config, "base")
    with migrated_engine.connect() as conn:
        tables = (
            conn.execute(
                text(
                    "select table_name from information_schema.tables where table_schema = 'public'"
                )
            )
            .scalars()
            .all()
        )
        enums = (
            conn.execute(
                text("select typname from pg_type where typname in ('user_role','user_status')")
            )
            .scalars()
            .all()
        )
    # alembic_version is Alembic's own bookkeeping and outlives downgrades.
    assert set(tables) == {"alembic_version"}
    assert enums == []
    command.upgrade(config, "head")


def test_constraint_names_follow_the_convention(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        names = set(
            conn.execute(
                text("select indexname from pg_indexes where tablename = 'users'")
            ).scalars()
        )
    assert {"pk_users", "uq_users_email", "ix_users_role_active"} <= names


def test_all_timestamp_columns_are_timezone_aware(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        naive = (
            conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name = 'users' "
                    "and data_type = 'timestamp without time zone'"
                )
            )
            .scalars()
            .all()
        )
    assert naive == [], f"naive timestamp columns: {naive}"


def _insert_user(conn: object, email: str, deleted: bool = False) -> uuid.UUID:
    user_id = uuid.uuid7()
    conn.execute(  # type: ignore[attr-defined]
        text(
            "insert into users (id, email, password_hash, first_name, last_name,"
            " deleted_at) values (:id, :email, 'x', 'Ada', 'Byron',"
            " case when :deleted then now() else null end)"
        ),
        {"id": user_id, "email": email, "deleted": deleted},
    )
    return user_id


def test_email_uniqueness_is_enforced_for_live_accounts(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as conn:
        _insert_user(conn, "ada@example.org")
        with pytest.raises(IntegrityError):
            _insert_user(conn, "ADA@example.org")  # citext: same address


def test_a_deleted_account_frees_its_email(migrated_engine: Engine) -> None:
    """The reason the unique index is partial.

    A plain UNIQUE would let a deleted account hold its address forever, so the
    person could never register again.
    """
    with migrated_engine.begin() as conn:
        _insert_user(conn, "ada@example.org", deleted=True)
        _insert_user(conn, "ada@example.org")  # must not raise

    with migrated_engine.connect() as conn:
        live = conn.execute(text("select count(*) from users where deleted_at is null")).scalar()
        total = conn.execute(text("select count(*) from users")).scalar()
    assert (live, total) == (1, 2)


def test_role_and_status_defaults(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        _insert_user(conn, "new@example.org")
        row = conn.execute(
            text("select role, status, token_version from users where email = 'new@example.org'")
        ).one()
    assert row == ("viewer", "active", 0)
