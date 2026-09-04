"""Request correlation."""

from flask.testing import FlaskClient

from app.logging import REQUEST_ID_HEADER


def test_every_response_carries_a_request_id(client: FlaskClient) -> None:
    response = client.get("/health")
    assert response.headers[REQUEST_ID_HEADER]


def test_an_incoming_request_id_is_preserved(client: FlaskClient) -> None:
    """So a trace survives across a proxy or another service."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


def test_ids_are_distinct_between_requests(client: FlaskClient) -> None:
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]
    assert first != second


def test_an_oversized_incoming_id_is_truncated(client: FlaskClient) -> None:
    """It is attacker-controlled and ends up in every log line for the request."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "x" * 5000})
    assert len(response.headers[REQUEST_ID_HEADER]) == 200


def test_error_responses_carry_it_too(client: FlaskClient) -> None:
    response = client.get("/no/such/route")
    assert response.headers[REQUEST_ID_HEADER]
