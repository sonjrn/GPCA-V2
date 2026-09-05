"""Authentication and account services.

Business rules live here rather than in routes, so they are testable without a
request context and reusable from CLI commands.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from flask import current_app, g
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.email import send_email
from app.security.passwords import build_hasher, dummy_verify, hash_password
from app.security.tokens import hash_refresh_token
from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, User

logger = logging.getLogger(__name__)

EMAIL_VERIFY_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)

# Long enough that guessing is not a strategy; short enough to survive an email
# client wrapping the link.
SINGLE_USE_TOKEN_BYTES = 32


def get_hasher(settings: Settings | None = None) -> PasswordHasher:
    resolved = settings or current_app.config["SETTINGS"]
    return build_hasher(
        memory_cost=resolved.argon2_memory_cost_kib,
        time_cost=resolved.argon2_time_cost,
        parallelism=resolved.argon2_parallelism,
    )


def find_by_email(session: Session, email: str) -> User | None:
    """Live accounts only. A soft-deleted row must not answer a lookup."""
    return session.scalars(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    ).one_or_none()


def issue_single_use_token(
    session: Session,
    *,
    user: User,
    purpose: AuthTokenPurpose,
    ttl: timedelta,
) -> str:
    """Mint a token, invalidating any outstanding one for the same purpose.

    The partial unique index permits only one live row per (user, purpose), so
    the previous token is consumed rather than left working -- otherwise every
    "resend" click leaves another valid link in circulation.
    """
    now = datetime.now(UTC)
    outstanding = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
        )
    ).all()
    for row in outstanding:
        row.consumed_at = now
    session.flush()

    plaintext = secrets.token_urlsafe(SINGLE_USE_TOKEN_BYTES)
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_refresh_token(plaintext),
            expires_at=now + ttl,
        )
    )
    return plaintext


def register(
    session: Session, *, email: str, password: str, first_name: str, last_name: str
) -> None:
    """Create an account, or pretend to.

    The response is identical whether or not the address is already
    registered, so this returns nothing a caller could branch on. Both paths
    hash a password: skipping it on the duplicate branch would make that path
    measurably faster and turn a uniform response into a timing oracle.
    """
    hasher = get_hasher()
    existing = find_by_email(session, email)

    if existing is not None:
        dummy_verify(hasher)
        logger.info("registration attempted for an existing address")
        _deferred_email(
            to=email,
            template="register_existing_account",
            context={"first_name": existing.first_name},
        )
        return

    user = User(
        email=email,
        password_hash=hash_password(hasher, password),
        first_name=first_name,
        last_name=last_name,
        role=UserRole.VIEWER,
        status=UserStatus.ACTIVE,
        token_version=0,
    )
    session.add(user)
    session.flush()

    token = issue_single_use_token(
        session, user=user, purpose=AuthTokenPurpose.EMAIL_VERIFY, ttl=EMAIL_VERIFY_TTL
    )
    _deferred_email(
        to=email,
        template="verify_email",
        context={"first_name": first_name, "token": token},
    )


# Emails are queued during the unit of work and sent after commit, so a
# message never describes a transaction that rolled back. The queue lives on
# `g`, which is request-scoped: a module-level list would be shared across
# concurrent requests and threads.
_QUEUE_KEY = "pending_emails"


def _deferred_email(**message: object) -> None:
    queue: list[dict[str, object]] = g.setdefault(_QUEUE_KEY, [])
    queue.append(message)


def flush_pending_emails() -> None:
    """Send everything queued during this request. Called after commit.

    Each send is already best-effort (integrations.email never raises), so one
    failing message does not stop the rest.
    """
    for message in g.pop(_QUEUE_KEY, []):
        try:
            send_email(**message)
        except Exception:
            # integrations.email already swallows its own failures; this is the
            # outer guard for anything it cannot, because the operation this
            # message describes has already committed.
            logger.exception("failed to send queued email")


def find_user(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)
