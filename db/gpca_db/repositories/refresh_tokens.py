"""Queries against the refresh_tokens table.

Refresh tokens are database rows rather than self-contained JWTs precisely so
that a stolen one can be revoked, which makes these queries part of the
security design rather than plumbing. Whether a lookup miss is an attack or an
expiry is a service decision; this module only fetches and marks rows.
"""

from sqlalchemy.orm import Session

from gpca_db.models import RefreshToken

__all__ = ["add"]


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
