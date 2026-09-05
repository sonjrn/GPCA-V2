"""Liveness and readiness.

The distinction matters operationally: liveness failing gets the container
killed, readiness failing only takes it out of the load balancer.
"""

import json
import os

import pytest
from flask.testing import FlaskClient

from app import create_app
from tests.conftest import make_settings

DATABASE_URL = os.environ.get("DATABASE_URL")


def test_liveness_is_ok_while_the_database_is_down(client: FlaskClient) -> None:
    """The regression this guards against is a restart loop.

    If liveness checked the database, a brief outage would kill every
    container instead of leaving them up and waiting.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert json.loads(response.data)["status"] == "ok"


def test_readiness_reports_503_while_the_database_is_down(
    client: FlaskClient,
) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = json.loads(response.data)
    assert body["status"] == "degraded"
    assert body["checks"]["database"] is False


def test_readiness_does_not_hang_on_an_unreachable_database(
    client: FlaskClient,
) -> None:
    """libpq's default connect timeout is minutes; ours is bounded."""
    import time

    started = time.monotonic()
    client.get("/health/ready")
    assert time.monotonic() - started < 10


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_readiness_is_200_with_a_reachable_database() -> None:
    app = create_app(make_settings(database_url=DATABASE_URL))
    response = app.test_client().get("/health/ready")
    assert response.status_code == 200
    body = json.loads(response.data)
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True


def test_probes_are_not_under_the_api_prefix(client: FlaskClient) -> None:
    """They serve the runtime, so they must not move when /api/v2 arrives."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 404


def test_probe_shapes_are_documented(client: FlaskClient) -> None:
    """The probes return declared models, not ad-hoc dicts.

    Both statuses of the readiness probe are in the document, so an operator
    reading the spec sees the degraded shape too.
    """
    document = json.loads(client.get("/api/v1/openapi.json").data)
    assert set(document["paths"]["/health/ready"]["get"]["responses"]) >= {"200", "503"}
    assert "ReadinessStatus" in document["components"]["schemas"]
    assert "HealthStatus" in document["components"]["schemas"]
