"""Authentication endpoints."""

from flask import Blueprint, Response, current_app, request
from spectree import Response as SpecResponse

from app import errors, messages
from app.extensions import session_scope
from app.responses import ok, respond
from app.schemas.auth import (
    AcceptedResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    TokenPairResponse,
    VerifyEmailRequest,
)
from app.security.decorators import current_user, require_auth
from app.services import auth as auth_service
from app.validation import api_spec

bp = Blueprint("auth", __name__, url_prefix="/auth")


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

    return respond(AcceptedResponse(detail=messages.ACCEPTED), 202)


# Already-verified is a success, not an error: people double-click links and
# forward the email to themselves, and a scary failure for a no-op is worse
# than useless. An unredeemable token is still a 400 -- it genuinely did not
# work -- but says nothing about whether it ever existed. The wording lives in
# app.errors with every other client-visible string.


@bp.post("/verify-email")
@api_spec.validate(
    json=VerifyEmailRequest,
    resp=SpecResponse(HTTP_200=MessageResponse, HTTP_400=MessageResponse),
    tags=["auth"],
)
def verify_email() -> Response:
    payload: VerifyEmailRequest = request.context.json  # type: ignore[attr-defined]

    with session_scope() as session:
        redeemed = auth_service.verify_email(session, token=payload.token)

    if not redeemed:
        return respond(MessageResponse(detail=messages.VERIFY_FAILED), 400)
    return ok(MessageResponse(detail=messages.VERIFIED))


@bp.post("/verify-email/resend")
@require_auth
@api_spec.validate(resp=SpecResponse(HTTP_202=AcceptedResponse), tags=["auth"])
def resend_verification() -> Response:
    """Authenticated, so it cannot be used to mail arbitrary addresses."""
    user = current_user()

    with session_scope() as session:
        attached = session.merge(user)
        auth_service.resend_verification(session, user=attached)

    auth_service.flush_pending_emails()
    return respond(AcceptedResponse(detail=messages.ACCEPTED), 202)


@bp.post("/login")
@api_spec.validate(
    json=LoginRequest,
    resp=SpecResponse(HTTP_200=TokenPairResponse, HTTP_401=MessageResponse),
    tags=["auth"],
)
def login() -> Response:
    payload: LoginRequest = request.context.json  # type: ignore[attr-defined]
    settings = current_app.config["SETTINGS"]

    try:
        with session_scope() as session:
            access, refresh, expires_in = auth_service.authenticate(
                session,
                email=str(payload.email),
                password=payload.password,
                settings=settings,
                user_agent=request.headers.get("User-Agent"),
                ip=request.remote_addr,
            )
    except errors.AuthenticationFailed as exc:
        raise errors.Unauthorized(messages.LOGIN_FAILED) from exc

    return ok(TokenPairResponse(access_token=access, expires_in=expires_in, refresh_token=refresh))
