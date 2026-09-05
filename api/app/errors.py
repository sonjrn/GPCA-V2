"""Every error the backend can raise. The wording lives in app.messages.

Two kinds, kept apart deliberately:

1. **HTTP errors.** AppError subclasses. Raising one produces a problem+json
   response with that status (design 3.4). These are the only errors a client
   ever observes.
2. **Internal signals.** Raised by a service, caught by its caller, never
   rendered. A service says *what happened*; the caller decides what the
   client is told.

The signals are not AppError subclasses on purpose. Inheriting one would make
it possible to let a service exception escape to the error handler and become
a response nobody chose -- the split is what forces a route to translate.
"""

import logging
from typing import Any

from flask import Flask, Response, g, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)

ERROR_TYPE_BASE = "https://api.gpca.org/errors"


class AppError(Exception):
    """Base for errors that map to a deliberate HTTP response."""

    status: int = 500
    title: str = "Internal Server Error"
    slug: str = "internal-error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = errors


class BadRequest(AppError):
    status, title, slug = 400, "Bad Request", "bad-request"


class Unauthorized(AppError):
    status, title, slug = 401, "Unauthorized", "unauthorized"


class PaymentFailed(AppError):
    status, title, slug = 402, "Payment Failed", "payment-failed"


class Forbidden(AppError):
    status, title, slug = 403, "Forbidden", "forbidden"


class NotFound(AppError):
    status, title, slug = 404, "Not Found", "not-found"

    def __init__(self, resource: str, identifier: object = None) -> None:
        detail = (
            f"No {resource} with identifier {identifier!r}."
            if identifier is not None
            else f"No such {resource}."
        )
        super().__init__(detail)


class Conflict(AppError):
    status, title, slug = 409, "Conflict", "conflict"


class ValidationFailed(AppError):
    status, title, slug = 422, "Validation Error", "validation-error"


class TooManyRequests(AppError):
    status, title, slug = 429, "Too Many Requests", "rate-limited"


# --------------------------------------------------------------------------
# Internal signals. Never rendered; a caller translates each into an HTTP
# error above.
# --------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised at startup when the environment is incomplete or invalid.

    Never reaches a client: it happens before the app can serve anything.
    """


class TokenError(Exception):
    """Base for anything wrong with a presented token."""


class TokenExpired(TokenError):
    """Distinguished from other failures so a client knows to refresh."""


class TokenInvalid(TokenError):
    """Malformed, wrong signature, or wrong shape."""


class AuthenticationFailed(Exception):
    """Raised for every login failure, without distinguishing which.

    Wrong password, unknown address, suspended account and deleted account all
    produce this. Anything more specific turns login into an oracle for which
    addresses hold accounts.
    """


class RefreshRejected(Exception):
    """A refresh token that cannot be exchanged, for any reason.

    Unknown, expired and replayed all raise this one type. The reuse case
    additionally revokes the token's whole family before raising -- a side
    effect, not a different signal, because telling the two apart is exactly
    what the caller must not be able to do.
    """


def problem_response(
    *,
    status: int,
    title: str,
    slug: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> Response:
    body: dict[str, Any] = {
        "type": f"{ERROR_TYPE_BASE}/{slug}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    if (request_id := g.get("request_id")) is not None:
        body["request_id"] = request_id

    response = jsonify(body)
    response.status_code = status
    # RFC 9457 media type, not application/json, so clients can distinguish an
    # error body from a successful one without inspecting the payload.
    response.mimetype = "application/problem+json"
    return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(error: AppError) -> Response:
        return problem_response(
            status=error.status,
            title=error.title,
            slug=error.slug,
            detail=error.detail,
            errors=error.errors,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException) -> Response:
        # Werkzeug's own 404/405/413 and friends, rendered in the same shape.
        return problem_response(
            status=error.code or 500,
            title=error.name,
            slug=(error.name or "error").lower().replace(" ", "-"),
            detail=error.description,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(_error: Exception) -> Response:
        # Log the traceback with the request id, return nothing about it. An
        # exception message can carry a query, a path or a credential.
        logger.exception("unhandled exception", extra={"request_id": g.get("request_id")})
        return problem_response(
            status=500,
            title="Internal Server Error",
            slug="internal-error",
            detail="An unexpected error occurred.",
        )
