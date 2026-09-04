"""Reusable column types and constraint helpers.

The `Annotated` aliases below pair a Python type with a fully configured
column, so a model writes `Mapped[UUIDPk]` rather than repeating the same
`mapped_column(...)` arguments on every table.
"""

from typing import Annotated
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import CITEXT, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import mapped_column

__all__ = [
    "CITEXT",
    "TSVECTOR",
    "CaseInsensitiveEmail",
    "CountryCode",
    "Currency",
    "MoneyCents",
    "StateCode",
    "UUIDPk",
    "nonnegative_cents",
    "uuid7",
]

# Primary key. UUIDv7 is time-ordered, so inserts land at the right-hand edge of
# the B-tree instead of scattering across it the way v4 does -- the difference
# shows up as index bloat and write amplification once tables are large.
# Generated in Python rather than by the database so a new row's id is known
# before flush, which is what lets services build related rows in one unit of
# work. uuid7 is standard library as of Python 3.14, which is the floor this
# project targets.
UUIDPk = Annotated[
    UUID,
    mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7),
]

# Money is always integer minor units plus an explicit currency (12.3.2).
# Float would round, and Numeric invites arithmetic in the wrong places.
MoneyCents = Annotated[int, mapped_column(Integer)]
Currency = Annotated[str, mapped_column(String(3))]

# ISO 3166-1 alpha-2, and the normalized subdivision code used by the location
# filters. The human-readable state/province name is stored separately.
CountryCode = Annotated[str, mapped_column(String(2))]
StateCode = Annotated[str, mapped_column(String(3))]

# Case-insensitive so that a lookup by email matches regardless of how the
# address was typed, without every query remembering to lower() both sides.
CaseInsensitiveEmail = Annotated[str, mapped_column(CITEXT)]


def nonnegative_cents(*columns: str, table: str) -> CheckConstraint:
    """A CHECK asserting money columns are never negative.

    The naming convention renders `ck` names from an explicit constraint name,
    so one is always supplied here rather than left to PostgreSQL.
    """
    if not columns:
        raise ValueError("nonnegative_cents() requires at least one column")
    condition = " AND ".join(f"{column} >= 0" for column in columns)
    return CheckConstraint(condition, name=f"{table}_{'_'.join(columns)}_nonnegative")
