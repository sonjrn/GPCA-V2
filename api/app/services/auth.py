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
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AuthenticationFailed
from app.integrations.email import send_email
from app.security.passwords import (
    build_hasher,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.tokens import (
    generate_refresh_token,
    hash_refresh_token,
    issue_access_token,
)
from gpca_db.enums import AuthTokenPurpose, UserRole, UserStatus
from gpca_db.models import AuthToken, RefreshToken, User
from gpca_db.repositories import auth_tokens as auth_token_repo
from gpca_db.repositories import refresh_tokens as refresh_token_repo
from gpca_db.repositories import users as user_repo

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
    return user_repo.get_by_email(session, email)


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
    auth_token_repo.consume_outstanding(session, user_id=user.id, purpose=purpose, now=now)

    plaintext = secrets.token_urlsafe(SINGLE_USE_TOKEN_BYTES)
    auth_token_repo.add(
        session,
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_refresh_token(plaintext),
            expires_at=now + ttl,
        ),
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
    user_repo.add(session, user)

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


def consume_single_use_token(
    session: Session, *, token: str, purpose: AuthTokenPurpose
) -> User | None:
    """Redeem a token, or return None if it cannot be redeemed.

    `purpose` is checked explicitly rather than assumed from which endpoint
    was called: a verification token must not reset a password, and a reset
    token must not verify an address.
    """
    row = auth_token_repo.get_by_hash(session, hash_refresh_token(token))

    if row is None or row.purpose is not purpose:
        return None
    if row.consumed_at is not None:
        return None
    if row.expires_at <= datetime.now(UTC):
        return None

    user = user_repo.get(session, row.user_id)
    if user is None or user.deleted_at is not None:
        return None

    row.consumed_at = datetime.now(UTC)
    return user


def verify_email(session: Session, *, token: str) -> bool:
    """Mark an address verified. True when the token was redeemed."""
    user = consume_single_use_token(session, token=token, purpose=AuthTokenPurpose.EMAIL_VERIFY)
    if user is None:
        return False
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    return True


def resend_verification(session: Session, *, user: User) -> None:
    """Issue a fresh verification link.

    A no-op for an already-verified account: re-sending would be a way to make
    the application mail an arbitrary verified address on demand.
    """
    if user.email_verified_at is not None:
        return
    token = issue_single_use_token(
        session, user=user, purpose=AuthTokenPurpose.EMAIL_VERIFY, ttl=EMAIL_VERIFY_TTL
    )
    _deferred_email(
        to=user.email,
        template="verify_email",
        context={"first_name": user.first_name, "token": token},
    )


def find_user(session: Session, user_id: UUID) -> User | None:
    return user_repo.get(session, user_id)


def issue_session(
    session: Session,
    *,
    user: User,
    settings: Settings,
    family_id: UUID | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str, int]:
    """Mint an access token and a refresh token.

    Every authentication method ends here -- password today, federated login
    or passkeys later -- which is what keeps the session mechanics in one
    place regardless of how the caller proved who they are.
    """
    access_token, _expires_at = issue_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        user_id=user.id,
        role=user.role.value,
        token_version=user.token_version,
        ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    plaintext, digest = generate_refresh_token()

    row = RefreshToken(
        user_id=user.id,
        token_hash=digest,
        expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days),
        user_agent=(user_agent or None),
        ip=(ip or None),
    )
    # A login starts a new lineage; a rotation continues the existing one.
    if family_id is not None:
        row.family_id = family_id
    refresh_token_repo.add(session, row)

    return access_token, plaintext, settings.jwt_access_ttl_seconds


def authenticate(
    session: Session,
    *,
    email: str,
    password: str,
    settings: Settings,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str, int]:
    """Verify credentials and open a session, or raise AuthenticationFailed."""
    hasher = get_hasher(settings)
    user = find_by_email(session, email)

    if user is None or user.password_hash is None:
        # Burn comparable time. Without this the unknown-address branch skips
        # Argon2 and returns sooner, and the uniform error message is undone
        # by a stopwatch.
        dummy_verify(hasher)
        raise AuthenticationFailed

    if not verify_password(hasher, user.password_hash, password):
        raise AuthenticationFailed

    if user.status is not UserStatus.ACTIVE:
        raise AuthenticationFailed

    # Parameters may have been raised since this hash was written; upgrade it
    # now that the plaintext is in hand, rather than invalidating the password.
    if needs_rehash(hasher, user.password_hash):
        user.password_hash = hash_password(hasher, password)

    user.last_login_at = datetime.now(UTC)

    return issue_session(session, user=user, settings=settings, user_agent=user_agent, ip=ip)
