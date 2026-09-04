"""Errors render as RFC 9457 problem+json, and 500s leak nothing."""

import json

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.errors import Conflict, Forbidden, NotFound, ValidationFailed

SECRET_IN_MESSAGE = "connection string postgres://user:hunter2@db/gpca"


@pytest.fixture
def app_with_failing_routes(app: Flask) -> Flask:
    @app.get("/boom/not-found")
    def _not_found() -> None:
        raise NotFound("breeder listing", "vom-hausberg")

    @app.get("/boom/forbidden")
    def _forbidden() -> None:
        raise Forbidden("You do not own this listing.")

    @app.get("/boom/conflict")
    def _conflict() -> None:
        raise Conflict("That slug is taken.")

    @app.get("/boom/validation")
    def _validation() -> None:
        raise ValidationFailed(
            "1 field failed validation",
            errors=[{"field": "contact_email", "code": "value_error", "message": "bad"}],
        )

    @app.get("/boom/unexpected")
    def _unexpected() -> None:
        raise RuntimeError(SECRET_IN_MESSAGE)

    return app


@pytest.fixture
def failing_client(app_with_failing_routes: Flask) -> FlaskClient:
    return app_with_failing_routes.test_client()


@pytest.mark.parametrize(
    ("path", "status", "slug"),
    [
        ("/boom/not-found", 404, "not-found"),
        ("/boom/forbidden", 403, "forbidden"),
        ("/boom/conflict", 409, "conflict"),
        ("/boom/validation", 422, "validation-error"),
    ],
)
def test_app_errors_render_as_problem_json(
    failing_client: FlaskClient, path: str, status: int, slug: str
) -> None:
    response = failing_client.get(path)
    assert response.status_code == status
    assert response.mimetype == "application/problem+json"
    body = json.loads(response.data)
    assert body["status"] == status
    assert body["type"].endswith(f"/{slug}")
    assert body["title"]


def test_not_found_names_the_resource(failing_client: FlaskClient) -> None:
    body = json.loads(failing_client.get("/boom/not-found").data)
    assert "breeder listing" in body["detail"]
    assert "vom-hausberg" in body["detail"]


def test_validation_errors_carry_field_detail(failing_client: FlaskClient) -> None:
    body = json.loads(failing_client.get("/boom/validation").data)
    assert body["errors"][0]["field"] == "contact_email"


def test_unexpected_exception_is_generic_and_leaks_nothing(
    failing_client: FlaskClient,
) -> None:
    """An exception message can carry a query, a path, or a credential."""
    response = failing_client.get("/boom/unexpected")
    assert response.status_code == 500
    assert response.mimetype == "application/problem+json"
    raw = response.data.decode()
    assert "hunter2" not in raw
    assert "postgres://" not in raw
    assert "Traceback" not in raw
    body = json.loads(raw)
    assert body["detail"] == "An unexpected error occurred."


def test_problem_body_carries_the_request_id(failing_client: FlaskClient) -> None:
    """So a user reporting an error can be matched to its traceback."""
    response = failing_client.get("/boom/conflict")
    body = json.loads(response.data)
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_unknown_route_uses_the_same_shape(client: FlaskClient) -> None:
    """Werkzeug's own 404 must not return HTML."""
    response = client.get("/no/such/route")
    assert response.status_code == 404
    assert response.mimetype == "application/problem+json"
    assert json.loads(response.data)["status"] == 404


def test_wrong_method_uses_the_same_shape(client: FlaskClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert response.mimetype == "application/problem+json"
