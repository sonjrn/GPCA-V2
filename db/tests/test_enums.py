"""Every vocabulary must persist its *values*, not its member names."""

from enum import StrEnum

import pytest

from gpca_db import enums


def _paired() -> list[tuple[str, type[StrEnum], object]]:
    """Match each StrEnum to the PostgreSQL type object declared beside it."""
    pairs = []
    for name in dir(enums):
        attribute = getattr(enums, name)
        if (
            isinstance(attribute, type)
            and issubclass(attribute, StrEnum)
            and attribute is not StrEnum
        ):
            # UserRole -> user_role_enum
            snake = "".join(
                f"_{ch.lower()}" if ch.isupper() and i else ch.lower() for i, ch in enumerate(name)
            )
            pairs.append((snake, attribute, getattr(enums, f"{snake}_enum")))
    return pairs


@pytest.mark.parametrize(("type_name", "python_enum", "pg_type"), _paired())
def test_pg_type_stores_values_not_member_names(
    type_name: str,
    python_enum: type[StrEnum],
    pg_type: object,
) -> None:
    """The regression this guards against.

    Without `values_callable`, SQLAlchemy persists ``VIEWER`` while every query,
    fixture and JSON payload uses ``viewer``. Nothing fails loudly -- rows just
    stop matching.
    """
    expected = [member.value for member in python_enum]
    assert pg_type.enums == expected  # type: ignore[attr-defined]
    assert pg_type.name == type_name  # type: ignore[attr-defined]
    assert all(value.islower() for value in expected)


def test_every_enum_has_a_paired_pg_type() -> None:
    """A StrEnum with no type object would silently become a varchar column."""
    assert len(_paired()) == 11


def test_user_roles_are_ordered_by_capability() -> None:
    assert list(enums.UserRole) == [
        enums.UserRole.VIEWER,
        enums.UserRole.MEMBER,
        enums.UserRole.ADMIN,
    ]


def test_str_enums_compare_to_plain_strings() -> None:
    """StrEnum, not Enum, so a value read back from JSON compares directly."""
    assert enums.PublicationStatus.PUBLISHED == "published"
    assert f"{enums.OrderStatus.PENDING_PAYMENT}" == "pending_payment"
