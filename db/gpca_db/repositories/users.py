"""Queries against the users table.

Statement construction only. Whether a lookup miss means "register this
address" or "return 401" is a service decision, so every function here answers
a question and none of them make one.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpca_db.models import User

__all__ = ["add", "get", "get_by_email"]


def get(session: Session, user_id: UUID) -> User | None:
    """By primary key, including soft-deleted rows.

    Callers that must not see a removed account check `deleted_at` themselves;
    the token guards do, because a token outliving its account is a distinct
    condition from a token that was never valid.
    """
    return session.get(User, user_id)


def get_by_email(session: Session, email: str) -> User | None:
    """Live accounts only.

    A soft-deleted row must not answer a lookup: the address is released when
    the account is removed, which is the whole reason uq_users_email is
    partial. Matching is case-insensitive because the column is citext.
    """
    return session.scalars(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    ).one_or_none()


def add(session: Session, user: User) -> User:
    """Stage a new user and flush, so its generated id is readable.

    The flush is not a commit: the caller's transaction still decides whether
    any of this survives.
    """
    session.add(user)
    session.flush()
    return user
