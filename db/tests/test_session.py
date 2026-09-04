"""Engine and session-factory construction.

None of these tests reach a database: the point is that construction is lazy
and configuration arrives as arguments.
"""

from sqlalchemy import Engine

from gpca_db.session import build_engine, build_session_factory

UNREACHABLE_URL = "postgresql+psycopg://user:pw@127.0.0.1:1/nonexistent"


def test_engine_construction_does_not_connect() -> None:
    """Building an engine against an unreachable host must still succeed.

    If this ever starts connecting, importing the package would require a live
    database -- which would break migrations, tests and the CLI.
    """
    engine = build_engine(UNREACHABLE_URL)
    assert isinstance(engine, Engine)
    assert engine.pool.checkedout() == 0


def test_engine_enables_pre_ping_and_recycling() -> None:
    engine = build_engine(UNREACHABLE_URL)
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
    assert engine.pool._recycle == 1800  # type: ignore[attr-defined]


def test_session_factory_does_not_expire_on_commit() -> None:
    """Responses are serialized from models after commit (design 12.3).

    With the default, commit expires every attribute and serialization either
    re-queries or raises on a closed session.
    """
    factory = build_session_factory(build_engine(UNREACHABLE_URL))
    assert factory.kw["expire_on_commit"] is False


def test_session_factory_is_bound_to_the_given_engine() -> None:
    engine = build_engine(UNREACHABLE_URL)
    factory = build_session_factory(engine)
    assert factory.kw["bind"] is engine
