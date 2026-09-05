"""Session and single-use token tables."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from gpca_db.base import Base
from gpca_db.enums import AuthTokenPurpose, auth_token_purpose_enum
from gpca_db.types import UUIDPk, uuid7


class RefreshToken(Base):
    """One issued refresh token.

    Stored as a SHA-256 digest, never in plaintext: a database leak must not
    hand an attacker a set of live sessions.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)

    # Every token descended from one login shares a family. Detecting a
    # replayed token can then revoke the whole lineage rather than one row,
    # which is what stops an attacker keeping a valid descendant.
    family_id: Mapped[UUID] = mapped_column(default=uuid7)

    issued_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    # Set when this token is rotated, so a lineage can be walked.
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )

    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        Index("ix_refresh_tokens_user_id_revoked_at", "user_id", "revoked_at"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )


class AuthToken(Base):
    """A single-use token for email verification or password reset.

    One table for both, distinguished by `purpose`, which is checked wherever
    a token is consumed.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    purpose: Mapped[AuthTokenPurpose] = mapped_column(auth_token_purpose_enum)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)

    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        # Partial: only one live token per purpose per user, so issuing a new
        # one invalidates the outstanding link rather than leaving both live.
        Index(
            "uq_auth_tokens_user_purpose_live",
            "user_id",
            "purpose",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )


__all__ = ["AuthToken", "RefreshToken"]
