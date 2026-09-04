"""The naming convention is the reason this package pins a custom MetaData.

These tests compile real DDL rather than inspecting constraint objects, because
convention names are resolved at DDL-render time -- asserting on the rendered
statement is the only check that reflects what reaches PostgreSQL.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable

from gpca_db.base import NAMING_CONVENTION, Base, TimestampMixin


@pytest.fixture
def throwaway_base() -> type[DeclarativeBase]:
    """A base with the project's convention but its own metadata.

    Declaring probe tables on the real Base would leak them into the metadata
    Alembic autogenerates against.
    """

    class ProbeBase(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        # Mirrors the real Base, so these tests exercise the project's actual
        # annotation mapping rather than SQLAlchemy's defaults.
        type_annotation_map = Base.type_annotation_map

    return ProbeBase


def test_base_imports_without_a_database() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.tables == {}


def test_constraint_names_follow_the_convention(
    throwaway_base: type[DeclarativeBase],
) -> None:
    class Kennel(throwaway_base):  # type: ignore[misc, valid-type]
        __tablename__ = "kennel"
        id: Mapped[int] = mapped_column(primary_key=True)
        slug: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"))
        dog_count: Mapped[int]

        __table_args__ = (
            UniqueConstraint("slug"),
            CheckConstraint("dog_count >= 0", name="dog_count_nonnegative"),
        )

    class Owner(throwaway_base):  # type: ignore[misc, valid-type]
        __tablename__ = "owner"
        id: Mapped[int] = mapped_column(primary_key=True)

    ddl = str(CreateTable(Kennel.__table__).compile(dialect=postgresql.dialect()))

    assert "CONSTRAINT pk_kennel PRIMARY KEY" in ddl
    assert "CONSTRAINT uq_kennel_slug UNIQUE" in ddl
    assert "CONSTRAINT ck_kennel_dog_count_nonnegative CHECK" in ddl
    assert "CONSTRAINT fk_kennel_owner_id_owner FOREIGN KEY" in ddl


def test_index_names_follow_the_convention(
    throwaway_base: type[DeclarativeBase],
) -> None:
    class Show(throwaway_base):  # type: ignore[misc, valid-type]
        __tablename__ = "show"
        id: Mapped[int] = mapped_column(primary_key=True)
        state_code: Mapped[str]

        __table_args__ = (Index(None, "state_code"),)

    index = next(iter(Show.__table__.indexes))
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "ix_show_state_code" in ddl


def test_timestamps_are_timezone_aware(
    throwaway_base: type[DeclarativeBase],
) -> None:
    """A naive timestamp column is the bug this mapping exists to prevent."""

    class Entry(TimestampMixin, throwaway_base):  # type: ignore[misc, valid-type]
        __tablename__ = "entry"
        id: Mapped[int] = mapped_column(primary_key=True)

    for column_name in ("created_at", "updated_at"):
        column = Entry.__table__.c[column_name]
        assert column.type.timezone is True, f"{column_name} is not timezone-aware"
        assert column.server_default is not None
        assert column.nullable is False


def test_annotation_map_yields_expected_column_types(
    throwaway_base: type[DeclarativeBase],
) -> None:
    class Record(throwaway_base):  # type: ignore[misc, valid-type]
        __tablename__ = "record"
        id: Mapped[int] = mapped_column(primary_key=True)
        occurred_at: Mapped[datetime]
        amount: Mapped[Decimal]
        payload: Mapped[dict[str, Any]]

    columns = Record.__table__.c
    assert columns.occurred_at.type.timezone is True
    assert isinstance(columns.payload.type, JSONB)
    assert isinstance(columns.amount.type, Numeric)
