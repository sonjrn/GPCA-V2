"""Queries against the refresh_tokens table.

Refresh tokens are database rows rather than self-contained JWTs precisely so
that a stolen one can be revoked, which makes these queries part of the
security design rather than plumbing. Whether a lookup miss is an attack or an
expiry is a service decision; this module only fetches and marks rows.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpca_db.models import RefreshToken

__all__ = ["add", "get_by_hash", "revoke_all_for_user", "revoke_family"]


def add(session: Session, token: RefreshToken) -> RefreshToken:
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


def get_by_hash(session: Session, token_hash: bytes) -> RefreshToken | None:
    """By digest, revoked or not.

    Deliberately unfiltered on `revoked_at`: finding an already-revoked row is
    how reuse is detected, so filtering it out here would quietly delete the
    feature this table exists for.
    """
    return session.scalars(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).one_or_none()


def revoke_family(session: Session, *, family_id: UUID, now: datetime) -> int:
    """Revoke every live token in one lineage. Returns how many.

    A whole family rather than one row: a replayed token means the chain is
    compromised, and leaving its newest descendant valid would leave whoever
    replayed it holding a working session.
    """
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


def revoke_all_for_user(session: Session, *, user_id: UUID, now: datetime) -> int:
    """Revoke every live token a user holds, across all families. Returns how many.

    The refresh half of "log me out everywhere". Bumping `token_version` is the
    other half and belongs to the caller, because it is a fact about the user
    rather than about these rows.
    """
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for row in rows:
        row.revoked_at = now
    return len(rows)
