"""The users table.

Account identity, role, and the location fields the member directory shows.
Authentication artifacts -- refresh tokens, verification and reset tokens --
land in their own tables with #5.
"""

from datetime import date, datetime

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from gpca_db.base import Base, TimestampMixin
from gpca_db.enums import UserRole, UserStatus, user_role_enum, user_status_enum
from gpca_db.types import CaseInsensitiveEmail, CountryCode, StateCode, UUIDPk


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUIDPk]

    # citext, so a lookup matches however the address was typed without every
    # query remembering to lower() both sides.
    email: Mapped[CaseInsensitiveEmail]
    password_hash: Mapped[str] = mapped_column(Text)

    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(150))

    role: Mapped[UserRole] = mapped_column(
        user_role_enum,
        default=UserRole.VIEWER,
        server_default=text(f"'{UserRole.VIEWER.value}'::user_role"),
    )
    status: Mapped[UserStatus] = mapped_column(
        user_status_enum,
        default=UserStatus.ACTIVE,
        server_default=text(f"'{UserStatus.ACTIVE.value}'::user_status"),
    )

    # Bumped to invalidate access tokens already issued, so a role change or a
    # suspension takes effect before the 15-minute token lifetime expires.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    email_verified_at: Mapped[datetime | None]
    phone: Mapped[str | None] = mapped_column(String(20))

    # Shown in the member directory. state_code is the normalized subdivision
    # for exact filtering; state_province keeps what the member typed.
    city: Mapped[str | None] = mapped_column(String(100))
    state_province: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[StateCode | None]
    country_code: Mapped[CountryCode | None]

    member_since: Mapped[date | None]
    last_login_at: Mapped[datetime | None]

    # Removal is a timestamp, not a status: `status` says what the account is
    # while it exists (active or suspended), this says whether it still does.
    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        # Partial, so a deleted account stops reserving its address and the
        # person can register again. A plain UNIQUE would block that forever.
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_users_role_active",
            "role",
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
        # Trigram index over the full name, for the sponsor picker's fuzzy
        # search.
        #
        # Two things here are deliberate and both are needed for `alembic
        # check` to actually verify this index rather than skip it:
        #
        # 1. The operator class lives in postgresql_ops, not inside the
        #    expression. With it inline, SQLAlchemy cannot compare the
        #    expression at all and assumes the index matches -- a permanent
        #    blind spot.
        # 2. The expression is written the way PostgreSQL normalizes it, with
        #    explicit ::text casts, because comparison is textual against
        #    pg_indexes. Written the natural way it never matches.
        #
        # If a future PostgreSQL release normalizes differently, `alembic
        # check` fails and this string needs updating. That is a loud,
        # fixable failure, which is why it is preferred over excluding the
        # index from comparison.
        Index(
            "ix_users_full_name_trgm",
            text("((first_name::text || ' '::text) || last_name::text)"),
            postgresql_using="gin",
            postgresql_ops={"((first_name::text || ' '::text) || last_name::text)": "gin_trgm_ops"},
        ),
    )
