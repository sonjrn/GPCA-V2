"""GPCA service layer.

Flask routes, wire schemas, business services and third-party integrations.
Imports gpca_db; nothing in gpca_db imports this package.
"""

from flask import Flask

from app.config import Settings, get_settings
from app.errors import register_error_handlers
from app.extensions import init_database
from app.logging import configure_logging, register_request_id
from app.routes.health import bp as health_bp
from app.validation import api_spec

__version__ = "0.1.0"

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> Flask:
    """Build an application.

    Configuration is resolved first: an incomplete environment raises here,
    at startup, rather than on the first request that needs the missing value.
    """
    settings = settings or get_settings()

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

    # Versioned blueprints register under API_PREFIX as they land.

    # Last, so the generated document sees every registered route. Serves the
    # spec at /api/v1/openapi.json alongside its Swagger and ReDoc UIs.
    api_spec.register(app)

    return app
