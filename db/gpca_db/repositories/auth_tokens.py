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

    The flush at the end is the one place this package still flushes by hand,
    and it is insurance rather than a fix. SQLAlchemy emits UPDATEs before
    INSERTs within a mapper, so the replacement INSERT lands after these rows
    are marked consumed and the partial unique index is satisfied -- but that
    ordering is observed behaviour, not a documented guarantee, and the cost
    of being wrong is a failed reissue. Flushing makes it explicit.
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
    """Stage a new row and flush. The caller still owns the transaction.

    Two reasons, both of which stop mattering only when someone remembers to
    flush by hand -- which is why it happens here instead:

    1. The primary key is a Core insert default, so it materializes during
       the INSERT. Before the flush, `row.id` is None.
    2. SQLAlchemy sorts pending inserts by ORM `relationship()`, and this
       schema declares none yet, so a raw ForeignKey column tells the unit of
       work nothing. Left to itself it will INSERT a child before its parent
       and take a foreign-key violation.

    Autoflush covers neither: it fires before a *query*, and neither reading
    an attribute nor staging another INSERT is one.

    A flush is not a commit; the caller's transaction still decides whether
    any of this survives.
    """
    session.add(token)
    session.flush()
    return token
