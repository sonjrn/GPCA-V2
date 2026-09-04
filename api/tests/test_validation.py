"""Request validation and the generated OpenAPI document.

Routes are defined inside the tests rather than exercising a real endpoint:
none exist yet, and these assertions are about the validation layer itself.
"""

import json

import pytest
from flask import Flask, request
from flask.testing import FlaskClient
from pydantic import Field
from spectree import Response as SpecResponse

from app.schemas import QueryModel, RequestModel, ResponseModel
from app.validation import api_spec


class ListingCreate(RequestModel):
    name: str = Field(max_length=20)
    dog_count: int


class ListingSearch(QueryModel):
    page: int = 1
    published: bool = False


class ListingRead(ResponseModel):
    name: str


@pytest.fixture
def validated_client(app: Flask) -> FlaskClient:
    @app.post("/probe/listings")
    @api_spec.validate(json=ListingCreate, resp=SpecResponse(HTTP_200=ListingRead))
    def _create() -> dict[str, str]:
        payload: ListingCreate = request.context.json
        return {"name": payload.name}

    @app.get("/probe/search")
    @api_spec.validate(query=ListingSearch)
    def _search() -> dict[str, object]:
        params: ListingSearch = request.context.query
        return {"page": params.page, "published": params.published}

    return app.test_client()


def test_a_valid_body_reaches_the_view(validated_client: FlaskClient) -> None:
    response = validated_client.post(
        "/probe/listings", json={"name": "Vom Hausberg", "dog_count": 4}
    )
    assert response.status_code == 200
    assert json.loads(response.data)["name"] == "Vom Hausberg"


def test_validation_failures_use_the_project_error_contract(
    validated_client: FlaskClient,
) -> None:
    """Validation must not be the one error shape that is not problem+json."""
    response = validated_client.post("/probe/listings", json={"name": "ok", "dog_count": "four"})
    assert response.status_code == 422
    assert response.mimetype == "application/problem+json"
    body = json.loads(response.data)
    assert body["type"].endswith("/validation-error")
    assert body["errors"][0]["field"] == "dog_count"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_unknown_fields_are_rejected_not_ignored(
    validated_client: FlaskClient,
) -> None:
    """A typo'd field must fail loudly rather than be silently dropped."""
    response = validated_client.post(
        "/probe/listings",
        json={"name": "ok", "dog_count": 1, "dogcount": 99},
    )
    assert response.status_code == 422
    fields = [error["field"] for error in json.loads(response.data)["errors"]]
    assert "dogcount" in fields


def test_request_bodies_are_strict_about_types(
    validated_client: FlaskClient,
) -> None:
    """A JSON client can send a real integer, so "4" is a caller-side bug."""
    response = validated_client.post("/probe/listings", json={"name": "ok", "dog_count": "4"})
    assert response.status_code == 422


def test_field_constraints_are_enforced(validated_client: FlaskClient) -> None:
    response = validated_client.post("/probe/listings", json={"name": "x" * 21, "dog_count": 1})
    assert response.status_code == 422
    assert json.loads(response.data)["errors"][0]["field"] == "name"


def test_query_parameters_are_coerced_from_text(
    validated_client: FlaskClient,
) -> None:
    """The reason QueryModel is not strict: a query string is all text."""
    response = validated_client.get("/probe/search?page=3&published=true")
    assert response.status_code == 200
    body = json.loads(response.data)
    assert body == {"page": 3, "published": True}


def test_query_defaults_apply_when_omitted(validated_client: FlaskClient) -> None:
    body = json.loads(validated_client.get("/probe/search").data)
    assert body == {"page": 1, "published": False}


def test_unknown_query_parameters_are_rejected(
    validated_client: FlaskClient,
) -> None:
    """A mistyped filter should fail rather than quietly return everything."""
    response = validated_client.get("/probe/search?pge=3")
    assert response.status_code == 422


def test_openapi_document_is_served(validated_client: FlaskClient) -> None:
    response = validated_client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    document = json.loads(response.data)
    assert document["info"]["title"] == "GPCA API"
    assert document["openapi"].startswith("3.")


def test_openapi_document_describes_validated_routes(
    validated_client: FlaskClient,
) -> None:
    """The document is generated from the same models the routes declare."""
    document = json.loads(validated_client.get("/api/v1/openapi.json").data)
    assert "/probe/listings" in document["paths"]
    assert "ListingCreate" in document["components"]["schemas"]
