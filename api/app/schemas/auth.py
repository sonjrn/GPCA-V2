"""Wire models for the auth endpoints."""

from pydantic import EmailStr, Field

from app.schemas.base import RequestModel, ResponseModel
from app.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class RegisterRequest(RequestModel):
    """A new account.

    Note what is absent: role, status, token_version, email_verified_at. A
    client cannot set them because there is nowhere to put them, which is a
    stronger guarantee than filtering them out later.
    """

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class AcceptedResponse(ResponseModel):
    """Deliberately uninformative.

    Registration and password-reset requests return this whether or not the
    address exists, so the response cannot be used to enumerate accounts.
    """

    detail: str


class VerifyEmailRequest(RequestModel):
    token: str = Field(min_length=1, max_length=512)


class MessageResponse(ResponseModel):
    detail: str
