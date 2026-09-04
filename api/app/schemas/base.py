"""Base classes for wire models.

These enact the two rules from docs/technical-design.md 2.3: request and
response models are distinct classes, and request models are strict. Every
schema in this package inherits one of them rather than BaseModel directly, so
the rules hold by construction instead of by review.
"""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """A JSON request body.

    `extra="forbid"` turns a typo'd field into a 422 rather than a silently
    ignored key -- the failure mode where a client thinks it set something and
    the server never saw it.

    `strict=True` refuses "2" where an int is expected. A JSON client can send
    a real integer, so accepting the string only hides a bug on the caller's
    side. Query parameters are the exception; see QueryModel.

    Fields a client must not set -- id, slug, status, owner ids -- are absent
    from these classes entirely, so mass assignment is impossible by
    construction rather than by a filter someone has to remember.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class QueryModel(BaseModel):
    """A query string.

    Same as RequestModel except that coercion stays on: everything in a query
    string arrives as text, so `?page=2` must be allowed to become an int.
    Strict mode here would reject every numeric and boolean parameter.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ResponseModel(BaseModel):
    """A response body, built from ORM objects or plain values.

    `from_attributes` allows `Model.model_validate(orm_object)` where the
    shapes line up. Where they do not, the response model gets an explicit
    from_model() classmethod instead -- never a to_dict() on the ORM class
    (2.3, rule 5).
    """

    model_config = ConfigDict(from_attributes=True)
