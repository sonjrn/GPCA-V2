"""Column aliases and constraint helpers."""

import uuid

import pytest
from sqlalchemy.orm import DeclarativeBase, Mapped

from gpca_db.types import MoneyCents, UUIDPk, nonnegative_cents, uuid7


def test_uuid7_is_version_7() -> None:
    assert uuid7().version == 7


def test_uuid7_values_are_time_ordered() -> None:
    """The property the primary key was chosen for.

    v4 keys scatter across the B-tree; v7 keys append, which is what keeps
    index writes cheap as tables grow.
    """
    generated = [uuid7() for _ in range(200)]
    assert generated == sorted(generated)


def test_uuid_pk_alias_configures_the_column() -> None:
    class ProbeBase(DeclarativeBase):
        pass

    class Widget(ProbeBase):
        __tablename__ = "widget"
        id: Mapped[UUIDPk]
        price_cents: Mapped[MoneyCents]

    id_column = Widget.__table__.c.id
    assert id_column.primary_key is True
    assert id_column.default is not None

    generated = id_column.default.arg(None)  # type: ignore[union-attr]
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7


def test_nonnegative_cents_builds_a_named_constraint() -> None:
    """The convention renders `ck` names from an explicit name, so one is
    always supplied -- an unnamed CHECK cannot be dropped by a downgrade."""
    constraint = nonnegative_cents("total_cents", table="orders")
    assert constraint.name == "orders_total_cents_nonnegative"
    assert str(constraint.sqltext) == "total_cents >= 0"


def test_nonnegative_cents_accepts_several_columns() -> None:
    constraint = nonnegative_cents("subtotal_cents", "tax_cents", table="orders")
    assert str(constraint.sqltext) == "subtotal_cents >= 0 AND tax_cents >= 0"


def test_nonnegative_cents_rejects_an_empty_column_list() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        nonnegative_cents(table="orders")
