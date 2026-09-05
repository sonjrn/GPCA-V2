"""Liveness and readiness probes.

Deliberately unversioned: these are infrastructure endpoints for the container
runtime and the load balancer, not part of the API contract clients program
against, so they must not move when /api/v2 arrives.
"""

import logging

from flask import Blueprint, Response
from spectree import Response as SpecResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import get_engine
from app.responses import ok, respond
from app.schemas.system import HealthStatus, ReadinessStatus
from app.validation import api_spec

logger = logging.getLogger(__name__)

bp = Blueprint("health", __name__)


@bp.get("/health")
@api_spec.validate(resp=SpecResponse(HTTP_200=HealthStatus), tags=["system"])
def liveness() -> Response:
    """Is the process up?

    Touches no dependency on purpose. A liveness probe that checks the
    database gets the container killed during a brief database blip, turning
    a recoverable outage into a restart loop.
    """
    return ok(HealthStatus())


@bp.get("/health/ready")
@api_spec.validate(
    resp=SpecResponse(HTTP_200=ReadinessStatus, HTTP_503=ReadinessStatus),
    tags=["system"],
)
def readiness() -> Response:
    """Can the process serve traffic?"""
    checks = {"database": _database_reachable()}
    healthy = all(checks.values())
    return respond(
        ReadinessStatus(status="ready" if healthy else "degraded", checks=checks),
        200 if healthy else 503,
    )


def _database_reachable() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("readiness: database unreachable", exc_info=True)
        return False
    return True
