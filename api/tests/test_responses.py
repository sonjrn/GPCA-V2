"""Standard success responses and the collection envelope."""

import json

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.responses import created, no_content, ok, respond
from app.schemas import Page, ResponseModel


class Dog(ResponseModel):
    name: str
    titles: int = 0


@pytest.fixture
def responding_client(app: Flask) -> FlaskClient:
    @app.get("/probe/one")
    def _one() -> object:
        return ok(Dog(name="Fritz", titles=3))

    @app.post("/probe/create")
    def _create() -> object:
        return created(Dog(name="Nia"), location="/api/v1/dogs/nia")

    @app.delete("/probe/gone")
    def _delete() -> object:
        return no_content()

    @app.get("/probe/degraded")
    def _degraded() -> object:
        return respond(Dog(name="x"), 503)

    @app.get("/probe/many")
    def _many() -> object:
        dogs = [Dog(name=f"dog-{n}") for n in range(3)]
        return ok(Page.of(dogs, page=2, per_page=3, total=7))

    return app.test_client()


def test_a_single_resource_is_returned_bare(responding_client: FlaskClient) -> None:
    """Not wrapped in an envelope -- clients would unwrap it for nothing."""
    response = responding_client.get("/probe/one")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert json.loads(response.data) == {"name": "Fritz", "titles": 3}


def test_created_carries_a_location_header(responding_client: FlaskClient) -> None:
    """How a client learns the assigned id without parsing the body."""
    response = responding_client.post("/probe/create")
    assert response.status_code == 201
    assert response.headers["Location"] == "/api/v1/dogs/nia"
    assert json.loads(response.data)["name"] == "Nia"


def test_no_content_has_an_empty_body(responding_client: FlaskClient) -> None:
    """A 204 carrying JSON is a contradiction some clients handle badly."""
    response = responding_client.delete("/probe/gone")
    assert response.status_code == 204
    assert response.data == b""


def test_respond_allows_an_explicit_status(responding_client: FlaskClient) -> None:
    response = responding_client.get("/probe/degraded")
    assert response.status_code == 503
    assert json.loads(response.data)["name"] == "x"


def test_collections_use_the_data_meta_envelope(
    responding_client: FlaskClient,
) -> None:
    body = json.loads(responding_client.get("/probe/many").data)
    assert set(body) == {"data", "meta"}
    assert len(body["data"]) == 3
    assert body["meta"] == {"page": 2, "per_page": 3, "total": 7, "total_pages": 3}


@pytest.mark.parametrize(
    ("total", "per_page", "expected"),
    [
        (0, 24, 0),
        (1, 24, 1),
        (24, 24, 1),
        (25, 24, 2),
        (48, 24, 2),
        (49, 24, 3),
    ],
)
def test_total_pages_is_derived_not_supplied(total: int, per_page: int, expected: int) -> None:
    """Derived so a caller cannot report a count inconsistent with the data.

    The 24/25 boundary is the one that gets written as `total // per_page` and
    silently loses the last page.
    """
    page = Page.of([], page=1, per_page=per_page, total=total)
    assert page.meta.total_pages == expected


def test_page_of_survives_a_zero_per_page() -> None:
    """Guards a division by zero on a degenerate query string."""
    assert Page.of([], page=1, per_page=0, total=10).meta.total_pages == 0


def test_response_models_serialize_json_unsafe_types() -> None:
    """UUIDs and datetimes must not reach the JSON encoder as objects."""
    from datetime import UTC, datetime
    from uuid import UUID

    class Record(ResponseModel):
        id: UUID
        created_at: datetime

    record = Record(
        id=UUID("0192f000-0000-7000-8000-000000000000"),
        created_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    dumped = record.model_dump(mode="json")
    assert dumped["id"] == "0192f000-0000-7000-8000-000000000000"
    assert dumped["created_at"].startswith("2026-09-04T12:00:00")
