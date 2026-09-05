"""Enumerated vocabularies, as Python StrEnums paired with PostgreSQL types.

Each vocabulary appears twice on purpose: the `StrEnum` is what application
code compares against, and the `ENUM` type object is what the column uses so
PostgreSQL rejects an invalid value at the database level.

The type objects are declared once here and referenced by models. Building an
`ENUM(...)` inline in a model instead produces a second, separately-named type
for the same vocabulary, which then diverges under migration.

Adding a value is a migration (`ALTER TYPE ... ADD VALUE`) that Alembic cannot
autogenerate -- see docs/technical-design.md 12.5.
"""

from enum import StrEnum

from sqlalchemy.dialects.postgresql import ENUM

__all__ = [
    "ApplicationStatus",
    "AuthTokenPurpose",
    "CartStatus",
    "ContentBlockType",
    "EndorsementStatus",
    "MediaStatus",
    "OrderStatus",
    "PaymentPurpose",
    "PaymentStatus",
    "PublicationStatus",
    "UserRole",
    "UserStatus",
    "application_status_enum",
    "auth_token_purpose_enum",
    "cart_status_enum",
    "content_block_type_enum",
    "endorsement_status_enum",
    "media_status_enum",
    "order_status_enum",
    "payment_purpose_enum",
    "payment_status_enum",
    "publication_status_enum",
    "user_role_enum",
    "user_status_enum",
]


def _pg_enum(python_enum: type[StrEnum], name: str) -> ENUM:
    """Build the PostgreSQL type for a StrEnum.

    `values_callable` is not optional. Without it SQLAlchemy persists the
    member *names* (``VIEWER``), while application code, JSON payloads and
    hand-written SQL all use the *values* (``viewer``). The mismatch surfaces
    much later as rows that no query matches.
    """
    return ENUM(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class UserRole(StrEnum):
    """Ordered by capability: viewer < member < admin."""

    VIEWER = "viewer"
    MEMBER = "member"
    ADMIN = "admin"


class UserStatus(StrEnum):
    """What the account is while it exists.

    Removal is not a status: an account that has been deleted carries
    `users.deleted_at`. Keeping the two axes apart means a suspended account is
    still a live account, and a deleted one records *when* -- which is what a
    retention or purge job needs to query.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


class ApplicationStatus(StrEnum):
    """Membership application lifecycle.

    `submitted` advances to `ready_for_review` only once the fee has been paid
    and both endorsements are accepted; those two events may arrive in either
    order.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class AuthTokenPurpose(StrEnum):
    """What a single-use `auth_tokens` row is for.

    Checked explicitly wherever a token is consumed: a verification token must
    not be usable to reset a password, and vice versa.
    """

    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"


class EndorsementStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELED = "canceled"


class PublicationStatus(StrEnum):
    """Whether content is live. Shared by listings, events, activities, products.

    Archival is not a status: withdrawn content carries `archived_at`, and on
    breeder listings an `archived_reason` that drives auto-restore when a
    revoked membership is re-granted. Splitting the axes means a *draft* can be
    archived too, which a three-value enum cannot express, and removes the
    redundancy of `status = 'archived'` sitting beside an `archived_at` column
    with nothing keeping them in agreement.

    Public reads therefore filter on both: `status = 'published' AND
    archived_at IS NULL`. That pairing lives in the repository loaders so no
    route can forget half of it.
    """

    DRAFT = "draft"
    PUBLISHED = "published"


class MediaStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class PaymentPurpose(StrEnum):
    MEMBERSHIP_APPLICATION = "membership_application"
    MERCH_ORDER = "merch_order"


class PaymentStatus(StrEnum):
    REQUIRES_PAYMENT = "requires_payment"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class CartStatus(StrEnum):
    ACTIVE = "active"
    CONVERTED = "converted"
    ABANDONED = "abandoned"


class ContentBlockType(StrEnum):
    TEXT = "text"
    RICH_TEXT = "rich_text"
    IMAGE = "image"
    LINK = "link"
    LIST = "list"


user_role_enum = _pg_enum(UserRole, "user_role")
user_status_enum = _pg_enum(UserStatus, "user_status")
application_status_enum = _pg_enum(ApplicationStatus, "application_status")
auth_token_purpose_enum = _pg_enum(AuthTokenPurpose, "auth_token_purpose")
endorsement_status_enum = _pg_enum(EndorsementStatus, "endorsement_status")
publication_status_enum = _pg_enum(PublicationStatus, "publication_status")
media_status_enum = _pg_enum(MediaStatus, "media_status")
payment_purpose_enum = _pg_enum(PaymentPurpose, "payment_purpose")
payment_status_enum = _pg_enum(PaymentStatus, "payment_status")
order_status_enum = _pg_enum(OrderStatus, "order_status")
cart_status_enum = _pg_enum(CartStatus, "cart_status")
content_block_type_enum = _pg_enum(ContentBlockType, "content_block_type")
