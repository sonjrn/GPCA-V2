"""Create the user_role and user_status types and the users table

Revision ID: 0002_users
Revises: 0001_extensions
Created: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_users"
down_revision: str | None = "0001_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the types are created and dropped explicitly below, so
# create_table does not also try to emit CREATE TYPE and fail on the second
# table that uses them.
user_role = postgresql.ENUM("viewer", "member", "admin", name="user_role", create_type=False)
user_status = postgresql.ENUM("active", "suspended", name="user_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind, checkfirst=False)
    user_status.create(bind, checkfirst=False)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=True),
        sa.Column(
            "role",
            user_role,
            server_default=sa.text("'viewer'::user_role"),
            nullable=False,
        ),
        sa.Column(
            "status",
            user_status,
            server_default=sa.text("'active'::user_status"),
            nullable=False,
        ),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("email_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state_province", sa.String(length=100), nullable=True),
        sa.Column("state_code", sa.String(length=3), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("member_since", sa.Date(), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )

    # Partial: a deleted account must stop reserving its address so the person
    # can register again. A plain UNIQUE would hold it forever.
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_users_role_active",
        "users",
        ["role"],
        unique=False,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    # Expression index -- autogenerate cannot produce or compare this one, so
    # it is written by hand and excluded from comparison in env.py.
    op.execute(
        "CREATE INDEX ix_users_full_name_trgm ON users "
        "USING gin ((first_name || ' ' || last_name) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_full_name_trgm")
    op.drop_index("ix_users_role_active", table_name="users")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    user_status.drop(bind, checkfirst=False)
    user_role.drop(bind, checkfirst=False)
