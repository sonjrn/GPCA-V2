"""Alembic environment.

Reads DATABASE_URL rather than alembic.ini so dev, CI and production run the
same migrations with no per-environment edits, and imports gpca_db.models so
`target_metadata` sees every table for autogenerate and `alembic check`.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from gpca_db import models  # noqa: F401 - imported for its side effect
from gpca_db.base import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silences every logger
    # that already exists -- including the application's, when migrations are
    # run in-process (the api test suite, or a CLI that migrates then serves).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Alembic takes the connection URL from the "
        "environment so the same migrations run everywhere unchanged."
    )
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Renders enum types as postgresql.ENUM in generated revisions rather
        # than the generic sa.Enum, which loses the native type name.
        include_schemas=False,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
