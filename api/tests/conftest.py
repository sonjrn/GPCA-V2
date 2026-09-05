"""Shared fixtures.

The default application points at an unreachable database on purpose: almost
everything here is about behaviour that must hold whether or not PostgreSQL is
up, and a short connect timeout keeps those tests fast.
"""

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.config import Settings

UNREACHABLE_DATABASE_URL = "postgresql+psycopg://user:pw@127.0.0.1:1/nonexistent"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "secret_key": "test-secret",
        "database_url": UNREACHABLE_DATABASE_URL,
        "jwt_secret": "test-jwt-secret",
        "db_connect_timeout_seconds": 1,
        "log_level": "CRITICAL",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def app() -> Iterator[Flask]:
    application = create_app(make_settings())
    yield application


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
