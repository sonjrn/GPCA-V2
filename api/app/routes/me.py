"""The authenticated user's own record."""

from flask import Blueprint, Response, current_app, request
from spectree import Response as SpecResponse

from app import errors, messages
from app.extensions import session_scope
from app.responses import ok
from app.schemas.auth import MessageResponse, TokenPairResponse
from app.schemas.me import ChangePasswordRequest, MeResponse, MeUpdate
from app.security.decorators import current_user, require_auth
from app.services import auth as auth_service
from app.validation import api_spec

bp = Blueprint("me", __name__, url_prefix="/me")


@bp.get("")
@require_auth
@api_spec.validate(resp=SpecResponse(HTTP_200=MeResponse), tags=["me"])
def read_me() -> Response:
    return ok(MeResponse.from_model(current_user()))


@bp.patch("")
@require_auth
@api_spec.validate(json=MeUpdate, resp=SpecResponse(HTTP_200=MeResponse), tags=["me"])
def update_me() -> Response:
    payload: MeUpdate = request.context.json  # type: ignore[attr-defined]
    user = current_user()

    # exclude_unset is the whole PATCH contract: without it every omitted
    # field would be dumped as None and the update would silently wipe
    # anything the client did not resend.
    changes = payload.model_dump(exclude_unset=True)

    with session_scope() as session:
        attached = session.merge(user)
        auth_service.update_profile(user=attached, changes=changes)
        # Built inside the scope, before the session closes and the attributes
        # go stale.
        body = MeResponse.from_model(attached)

    return ok(body)


@bp.post("/password")
@require_auth
@api_spec.validate(
    json=ChangePasswordRequest,
    resp=SpecResponse(HTTP_200=TokenPairResponse, HTTP_401=MessageResponse),
    tags=["me"],
)
def change_password() -> Response:
    """Returns a fresh token pair.

    The change revokes every session including this one, so the caller is
    handed a replacement rather than being signed out of the browser they are
    sitting in.
    """
    payload: ChangePasswordRequest = request.context.json  # type: ignore[attr-defined]
    settings = current_app.config["SETTINGS"]
    user = current_user()

    with session_scope() as session:
        attached = session.merge(user)
        issued = auth_service.change_password(
            session,
            user=attached,
            current_password=payload.current_password,
            new_password=payload.new_password,
            settings=settings,
            user_agent=request.headers.get("User-Agent"),
            ip=request.remote_addr,
        )
        if issued is None:
            # Raised inside the scope: nothing was changed, so the rollback
            # this triggers is the correct outcome.
            raise errors.Unauthorized(messages.WRONG_PASSWORD)

    auth_service.flush_pending_emails()

    access, refresh_token, expires_in = issued
    return ok(
        TokenPairResponse(access_token=access, expires_in=expires_in, refresh_token=refresh_token)
    )
