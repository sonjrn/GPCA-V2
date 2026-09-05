"""Wire models: request and response schemas. Never SQLAlchemy."""

from app.schemas.base import Page, PageMeta, QueryModel, RequestModel, ResponseModel

__all__ = ["Page", "PageMeta", "QueryModel", "RequestModel", "ResponseModel"]
