"""Shared clients and the request-scoped database session.

The engine is built once per application and stored on it, rather than as a
module global, so tests can create several applications without them sharing
a connection pool.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from flask import Flask, current_app
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from gpca_db.session import build_engine, build_session_factory

ENGINE_KEY = "gpca_engine"
SESSION_FACTORY_KEY = "gpca_session_factory"

# Redis, S3 and SES clients are initialized here alongside the engine when the
# features that need them land (#6 onward).


def init_database(app: Flask, settings: Settings) -> None:
    engine = build_engine(
        settings.database_url,
        echo=settings.is_debug,
        connect_timeout_seconds=settings.db_connect_timeout_seconds,
    )
    app.extensions[ENGINE_KEY] = engine
    app.extensions[SESSION_FACTORY_KEY] = build_session_factory(engine)


def get_engine() -> Engine:
    engine: Engine = current_app.extensions[ENGINE_KEY]
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transaction around a unit of work.

    Commits on success, rolls back on any exception, always closes. Response
    models are built inside this scope, before the session goes away.
    """
    factory: sessionmaker[Session] = current_app.extensions[SESSION_FACTORY_KEY]
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
