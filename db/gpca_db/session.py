"""Engine and session-factory construction.

Both take their configuration as arguments. Nothing here reads an environment
variable, and no engine is built at import time -- the API layer owns the
lifecycle and passes the URL in (docs/technical-design.md 12.2). That is what
lets tests, migrations and background workers each construct their own.
"""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["build_engine", "build_session_factory", "is_reachable"]


def build_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_recycle_seconds: int = 1800,
    pool_pre_ping: bool = True,
    connect_timeout_seconds: int = 10,
) -> Engine:
    """Create an Engine for `url`.

    `pool_pre_ping` costs one round trip per checkout and buys immunity to
    connections killed underneath the pool -- by a database restart, a deploy,
    or an idle timeout on a managed instance. Without it those surface as a
    random failed request rather than a transparent reconnect.

    `pool_recycle_seconds` retires connections before infrastructure between
    the app and PostgreSQL decides to.

    `connect_timeout_seconds` bounds how long a connection attempt may block.
    libpq's default is minutes, which would let a readiness probe hang instead
    of reporting the database unreachable.
    """
    return create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle_seconds,
        pool_pre_ping=pool_pre_ping,
        connect_args={"connect_timeout": connect_timeout_seconds},
        future=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the session factory bound to `engine`.

    `expire_on_commit=False` matters for the response path: with the default,
    committing expires every loaded attribute, so serializing a model after
    commit would re-query -- or fail outright once the session is closed.
    Keeping attributes alive is what lets a route commit and then build its
    response model from the object it already has (12.3).
    """
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def is_reachable(engine: Engine) -> bool:
    """Can a connection be checked out and used? For the readiness probe.

    Lives here rather than in repositories/ because it asks about the
    connection rather than about any table -- but it is still SQL, and SQL
    does not belong in a route (design 2.3, rule 4).

    Swallows SQLAlchemyError deliberately: a probe reports a boolean, and an
    unreachable database is the answer it exists to give, not an error.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True
