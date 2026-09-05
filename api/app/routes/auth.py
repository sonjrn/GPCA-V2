"""Authentication endpoints."""

from flask import Blueprint, Response, request
from spectree import Response as SpecResponse

from app.extensions import session_scope
from app.responses import respond
from app.schemas.auth import AcceptedResponse, RegisterRequest
from app.services import auth as auth_service
from app.validation import api_spec

bp = Blueprint("auth", __name__, url_prefix="/auth")

# The same body for every outcome. A distinct response for "already
# registered" would turn this endpoint into a membership oracle for the whole
# club roster.
_ACCEPTED = "If that address can be registered, we have sent a message to it."


@bp.post("/register")
@api_spec.validate(
    json=RegisterRequest,
    resp=SpecResponse(HTTP_202=AcceptedResponse),
    tags=["auth"],
)
def register() -> Response:
    payload: RegisterRequest = request.context.json  # type: ignore[attr-defined]

    with session_scope() as session:
        auth_service.register(
            session,
            email=str(payload.email),
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )

    # After commit: an email about an account whose transaction rolled back is
    # worse than no email at all.
    auth_service.flush_pending_emails()

    return respond(AcceptedResponse(detail=_ACCEPTED), 202)
