"""Standard success responses.

The counterpart to errors.py: every successful response is built here, so the
shape of a 200, a 201 and a 204 is decided once rather than at each route.

The convention (design 3.3) is that a single resource is returned bare and a
collection is wrapped in `Page`. These helpers take a Pydantic model rather
than a dict, which is what keeps a route from inventing an ad-hoc payload that
never appears in the OpenAPI document.
"""

from http import HTTPStatus

from flask import Response, jsonify
from pydantic import BaseModel

__all__ = ["created", "no_content", "ok", "respond"]


def respond(payload: BaseModel, status: int = HTTPStatus.OK) -> Response:
    """Serialize a response model at an explicit status.

    `mode="json"` so datetimes, UUIDs and enums land as strings rather than
    tripping the JSON encoder.
    """
    response = jsonify(payload.model_dump(mode="json"))
    response.status_code = status
    return response


def ok(payload: BaseModel) -> Response:
    """200 with a body."""
    return respond(payload, HTTPStatus.OK)


def created(payload: BaseModel, *, location: str | None = None) -> Response:
    """201 with a body, and a Location header pointing at the new resource.

    The header is part of the contract for a create, not decoration: it is how
    a client learns the assigned slug or id without parsing the body.
    """
    response = respond(payload, HTTPStatus.CREATED)
    if location:
        response.headers["Location"] = location
    return response


def no_content() -> Response:
    """204 with no body, for deletes and successful no-op updates.

    Deliberately empty: a 204 carrying JSON is a contradiction some clients
    handle badly.
    """
    return Response(status=HTTPStatus.NO_CONTENT)
