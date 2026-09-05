"""Wire models for the probes.

Typed rather than ad-hoc dicts so the shapes appear in the OpenAPI document
and cannot drift from what the routes actually return.
"""

from typing import Literal

from app.schemas.base import ResponseModel


class HealthStatus(ResponseModel):
    """Liveness. Deliberately carries nothing about dependencies."""

    status: Literal["ok"] = "ok"


class ReadinessStatus(ResponseModel):
    """Readiness, with the per-dependency detail an operator needs.

    `checks` names each dependency so a 503 says *which* one is down rather
    than only that something is.
    """

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]
