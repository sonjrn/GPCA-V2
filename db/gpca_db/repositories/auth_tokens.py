"""Queries against the auth_tokens table.

Single-use tokens for email verification and password reset. Both purposes
share the table and are told apart by `purpose`, which is why every lookup
here takes one -- see consume_outstanding and get_by_hash.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpca_db.enums import AuthTokenPurpose
from gpca_db.models import AuthToken

__all__ = ["add", "consume_outstanding", "get_by_hash"]


def get_by_hash(session: Session, token_hash: bytes) -> AuthToken | None:
    """By digest, whatever its purpose or state.

    Deliberately unfiltered: the caller checks purpose, expiry and consumption
    itself, because "expired" and "already used" and "wrong purpose" are the
    same 400 to a client but different lines in a log.
    """
    return session.scalars(
        select(AuthToken).where(AuthToken.token_hash == token_hash)
    ).one_or_none()


def consume_outstanding(
    session: Session, *, user_id: UUID, purpose: AuthTokenPurpose, now: datetime
) -> int:
    """Mark every live token for this (user, purpose) consumed. Returns how many.

    Called before minting a replacement, because the partial unique index
    permits only one live row per pair -- and because leaving the previous
    link working would mean every "resend" click adds another valid one to
    someone's inbox.

    `now` is passed in rather than read here so one unit of work stamps every
    row it touches with the same instant.
    """
    rows = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
        )
    ).all()
    for row in rows:
        row.consumed_at = now
    session.flush()
    return len(rows)


def add(session: Session, token: AuthToken) -> AuthToken:
    """Stage a freshly minted token. The caller owns the transaction."""
    session.add(token)
    return token
