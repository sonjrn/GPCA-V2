"""Wire models: request and response schemas. Never SQLAlchemy."""

from app.schemas.base import QueryModel, RequestModel, ResponseModel

__all__ = ["QueryModel", "RequestModel", "ResponseModel"]
