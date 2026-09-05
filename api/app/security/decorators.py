"""Route guards.

Each raises the existing AppError types, so an unauthorized request renders as
problem+json through the normal handler with no special-casing at the route.
"""

import functools
from collections.abc import Callable
from typing import Any, cast

from flask import current_app, g, request

from app.errors import Forbidden, Unauthorized
from app.extensions import session_scope
from app.security.tokens import TokenError, TokenExpired, decode_access_token
from gpca_db.enums import UserRole, UserStatus
from gpca_db.models import User

# viewer < member < admin. An ordered comparison, not set membership: an admin
# satisfies a member requirement without anyone maintaining a list.
_ROLE_RANK = {UserRole.VIEWER: 0, UserRole.MEMBER: 1, UserRole.ADMIN: 2}


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("A bearer token is required.")
    return token.strip()


def _load_current_user() -> User:
    """Resolve the caller from the access token, or raise.

    The database read here is deliberate. `token_version` is carried in the
    token and compared against the column, which is what makes a role change,
    a suspension or a logout-everywhere take effect immediately rather than
    when the 15-minute token happens to expire. Redis is not in this design
    (section 11), so this is one indexed primary-key lookup.
    """
    settings = current_app.config["SETTINGS"]
    try:
        claims = decode_access_token(
            secret=settings.jwt_secret.get_secret_value(), token=_bearer_token()
        )
    except TokenExpired as exc:
        raise Unauthorized("Access token has expired.") from exc
    except TokenError as exc:
        raise Unauthorized("Access token is not valid.") from exc

    with session_scope() as session:
        user = session.get(User, claims.user_id)
        if user is None or user.deleted_at is not None:
            raise Unauthorized("Access token is not valid.")
        if user.status is not UserStatus.ACTIVE:
            raise Unauthorized("Access token is not valid.")
        if user.token_version != claims.token_version:
            # Superseded by a role change, a password reset, or logout-all.
            raise Unauthorized("Access token is no longer valid.")
        return user


def require_auth[F: Callable[..., Any]](view: F) -> F:
    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        g.current_user = _load_current_user()
        return view(*args, **kwargs)

    return cast(F, wrapper)


def require_role[F: Callable[..., Any]](minimum: UserRole) -> Callable[[F], F]:
    def decorate(view: F) -> F:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = _load_current_user()
            if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
                raise Forbidden(f"This action requires the {minimum.value} role.")
            g.current_user = user
            return view(*args, **kwargs)

        return cast(F, wrapper)

    return decorate


def require_admin[F: Callable[..., Any]](view: F) -> F:
    return require_role(UserRole.ADMIN)(view)


def require_verified_email[F: Callable[..., Any]](view: F) -> F:
    """For actions gated on a confirmed address (section 4.4).

    An unverified account may browse and buy; it may not apply for membership,
    endorse an applicant, or edit a listing.
    """

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = _load_current_user()
        if user.email_verified_at is None:
            raise Forbidden("Verify your email address to do this.")
        g.current_user = user
        return view(*args, **kwargs)

    return cast(F, wrapper)


def current_user() -> User:
    """The user resolved by a guard on this request."""
    user = g.get("current_user")
    if user is None:
        raise Unauthorized("No authenticated user on this request.")
    return cast(User, user)


__all__ = [
    "current_user",
    "require_admin",
    "require_auth",
    "require_role",
    "require_verified_email",
]
