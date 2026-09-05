"""Request validation and the generated OpenAPI document.

Pydantic is the validation library for this project (design 2.1). SpecTree
supplies the Flask glue -- a decorator over ordinary blueprints, plus an
OpenAPI document generated from the same models the routes already declare.

Chosen over hand-rolling the decorator because it is roughly 150 lines we
would otherwise own and keep in step with the OpenAPI spec, and over
flask-openapi3 because SpecTree layers onto plain Flask routing rather than
replacing the Flask application class.

Usage:

    @bp.post("/things")
    @api_spec.validate(json=ThingCreate, resp=Response(HTTP_201=ThingRead))
    def create_thing() -> tuple[dict, int]:
        payload: ThingCreate = request.context.json
        ...
"""

from typing import Any

from pydantic import BaseModel, ValidationError
from spectree import SpecTree

from app.errors import ValidationFailed

# Where the document and its UIs are served. Matches design 3.5: the spec
# lands at /api/v1/openapi.json.
SPEC_PATH = "api/v1"


def _raise_as_problem_json(
    _req: Any,
    _resp: Any,
    req_validation_error: ValidationError | None,
    _instance: Any,
) -> None:
    """Turn SpecTree's validation failure into our error contract.

    Without this hook SpecTree returns its own JSON body, which would make
    validation the one error in the API that is not RFC 9457 problem+json.
    """
    if req_validation_error is None:
        return

    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "code": error["type"],
            "message": error["msg"],
        }
        for error in req_validation_error.errors()
    ]
    plural = "s" if len(errors) != 1 else ""
    raise ValidationFailed(f"{len(errors)} field{plural} failed validation", errors=errors)


def _schema_name(model: type[BaseModel]) -> str:
    """Name OpenAPI components after the model, with no hash suffix.

    SpecTree defaults to `ListingCreate.9a68e01`, hashing the module path so
    the code structure does not leak. That suffix reaches generated clients as
    a class name, so the plain name is used instead.

    The tradeoff: schema class names must be unique across the project, since
    two models sharing a name would collide in the document. Given the naming
    convention -- one Create/Update/Read set per resource -- that is a rule
    worth keeping anyway.
    """
    return model.__name__


# A module-level singleton by necessity: the decorator is applied when a
# blueprint module is imported, so the instance must exist at import time.
#
# It memoizes the generated document on first access, which is what you want
# in production -- routes are all registered before the first request -- but
# means a route added after that first request would be missing from the
# document, and that tests must clear the cache between application instances.
api_spec = SpecTree(
    "flask",
    title="GPCA API",
    version="1.0.0",
    path=SPEC_PATH,
    before=_raise_as_problem_json,
    naming_strategy=_schema_name,
    # The status is set by ValidationFailed; this keeps SpecTree's own
    # documented error responses consistent with what actually happens.
    validation_error_status=422,
)
