"""Session and single-use token tables; make password_hash nullable

Revision ID: 0003_auth_tokens
Revises: 0002_users
Created: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_auth_tokens"
down_revision: str | None = "0002_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

auth_token_purpose = postgresql.ENUM(
    "email_verify", "password_reset", name="auth_token_purpose", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()

    # A federated account (sign in with Google) has no password. Making this
    # nullable now is a one-word change; doing it later, with rows in place,
    # is not.
    #
    # There is deliberately no CHECK enforcing "has a password or a linked
    # identity": PostgreSQL CHECK constraints cannot reference another table,
    # so that rule lives in the service layer.
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)

    auth_token_purpose.create(bind, checkfirst=False)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name=op.f("fk_refresh_tokens_replaced_by_id_refresh_tokens"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index(
        "ix_refresh_tokens_user_id_revoked_at",
        "refresh_tokens",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", auth_token_purpose, nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_tokens")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_tokens_token_hash")),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    # Only one live token per purpose per user: issuing a new one must
    # invalidate the outstanding link rather than leaving both usable.
    op.create_index(
        "uq_auth_tokens_user_purpose_live",
        "auth_tokens",
        ["user_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_auth_tokens_user_purpose_live", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    op.drop_index("ix_refresh_tokens_user_id_revoked_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    auth_token_purpose.drop(op.get_bind(), checkfirst=False)

    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
