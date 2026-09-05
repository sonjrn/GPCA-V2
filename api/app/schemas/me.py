"""Wire models for the authenticated user's own record."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import RequestModel, ResponseModel
from app.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from gpca_db.models import User


class MeResponse(ResponseModel):
    """The caller's own record.

    Every field is listed explicitly and the object is built by `from_model`,
    not `model_validate(user)`. The User row carries `password_hash`,
    `token_version`, `status` and `deleted_at`; none of them belong in a
    response, and an explicit list is what keeps a column added later from
    appearing here by accident.
    """

    id: UUID
    email: str
    first_name: str
    last_name: str
    display_name: str | None
    role: str

    # A boolean, not the timestamp. When someone confirmed their address is
    # not the client's business; whether they have is.
    email_verified: bool

    phone: str | None
    city: str | None
    state_province: str | None
    state_code: str | None
    country_code: str | None
    member_since: date | None
    created_at: datetime

    @classmethod
    def from_model(cls, user: User) -> Self:
        return cls(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            display_name=user.display_name,
            role=user.role.value,
            email_verified=user.email_verified_at is not None,
            phone=user.phone,
            city=user.city,
            state_province=user.state_province,
            state_code=user.state_code,
            country_code=user.country_code,
            member_since=user.member_since,
            created_at=user.created_at,
        )


class MeUpdate(RequestModel):
    """A partial update of the caller's own profile.

    Note what is absent: `email`, `role`, `status`, `token_version`,
    `member_since`. They are not fields on this class, so `extra="forbid"`
    turns an attempt to set one into a 422 rather than a silent no-op.
    Changing an address is a separate flow that has to re-verify it.

    Every field defaults to None so `exclude_unset=True` can tell "not sent"
    from "sent as null" -- clearing a phone number and not mentioning it are
    different requests.
    """

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    state_province: str | None = Field(default=None, max_length=100)
    state_code: str | None = Field(default=None, max_length=3)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("first_name", "last_name")
    @classmethod
    def _reject_explicit_null(cls, value: str | None) -> str | None:
        """These two are NOT NULL in the database.

        Defaults are not validated, so this only fires when the client
        actually sent `null` -- which is a 422 naming the field rather than an
        IntegrityError from the flush.
        """
        if value is None:
            raise ValueError("This field cannot be cleared.")
        return value

    @field_validator("state_code", "country_code")
    @classmethod
    def _upcase(cls, value: str | None) -> str | None:
        """Stored normalized, so the directory's location filter matches
        whether someone typed "us" or "US"."""
        return value.upper() if value else value


class ChangePasswordRequest(RequestModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
