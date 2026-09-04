"""Enable the PostgreSQL extensions the schema depends on

Revision ID: 0001_extensions
Revises:
Created: 2026-09-04

Its own revision because every later migration assumes these types and
operators exist: citext for case-insensitive email, pg_trgm for the fuzzy
name search, pgcrypto for gen_random_uuid as a fallback, unaccent for
search normalization.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pgcrypto", "citext", "pg_trgm", "unaccent")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    # Reverse order is cosmetic here -- the extensions do not depend on each
    # other -- but DROP will fail loudly if anything still references a type,
    # which is the behaviour we want rather than a cascade.
    for extension in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')
