"""GPCA service layer.

Flask routes, wire schemas, business services and third-party integrations.
Imports gpca_db; nothing in gpca_db imports this package.
"""

from flask import Flask, jsonify
from flask.wrappers import Response

from app.config import Settings, load_settings
from app.errors import register_error_handlers
from app.extensions import init_database
from app.logging import configure_logging, register_request_id
from app.routes.auth import bp as auth_bp
from app.routes.health import bp as health_bp
from app.routes.me import bp as me_bp
from app.validation import api_spec

__version__ = "0.1.0"

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> Flask:
    """Build an application.

    Configuration is resolved first: an incomplete environment raises here,
    at startup, rather than on the first request that needs the missing value.

    Settings are read once and stored on the application, so anything needing
    them later reads `current_app.config["SETTINGS"]` rather than re-parsing
    the environment.
    """
    settings = settings or load_settings()

    configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["DEBUG"] = settings.is_debug
    # Let our own handler render 500s as problem+json rather than Flask
    # re-raising in debug mode and returning HTML.
    app.config["PROPAGATE_EXCEPTIONS"] = False

    init_database(app, settings)
    register_request_id(app)
    register_error_handlers(app)

    # Unversioned: probes are for the runtime, not for API clients.
    app.register_blueprint(health_bp)

    app.register_blueprint(auth_bp, url_prefix=f"{API_PREFIX}{auth_bp.url_prefix}")
    app.register_blueprint(me_bp, url_prefix=f"{API_PREFIX}{me_bp.url_prefix}")

    # Last, so the generated document sees every registered route. SpecTree
    # serves its UIs under /docs; see app/validation.py for why that cannot be
    # the API prefix.
    api_spec.register(app)

    # The spec at the URL the design documents. The endpoint name must start
    # with "openapi": that is how SpecTree recognises its own routes and keeps
    # this one out of the document it describes.
    def openapi_document() -> Response:
        return jsonify(api_spec.spec)

    app.add_url_rule(
        f"{API_PREFIX}/openapi.json",
        endpoint="openapi_document_alias",
        view_func=openapi_document,
        methods=["GET"],
    )

    return app
