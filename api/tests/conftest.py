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
from app.validation import api_spec

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


@pytest.fixture(autouse=True)
def _fresh_openapi_document() -> Iterator[None]:
    """Clear SpecTree's memoized document between tests.

    `api_spec` is a module-level singleton -- it has to be, since the decorator
    is applied when a blueprint module is imported. It caches the generated
    document on first access, so without this the first test app to request
    /api/v1/openapi.json would freeze the document for every later one, and a
    test asserting on its own routes would see someone else's.
    """
    api_spec.__dict__.pop("_spec", None)
    yield
    api_spec.__dict__.pop("_spec", None)


@pytest.fixture
def app() -> Iterator[Flask]:
    application = create_app(make_settings())
    yield application


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
