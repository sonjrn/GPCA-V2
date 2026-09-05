"""Structured logging and request correlation.

Every log line is a JSON object carrying the request id, so a failure can be
traced from the client's X-Request-ID header through to the traceback.
"""

import json
import logging
import sys
import uuid
from typing import Any

from flask import Flask, Response, g, request

REQUEST_ID_HEADER = "X-Request-ID"

# Attributes LogRecord always carries; anything else was passed via `extra`
# and belongs in the emitted object.
_STANDARD_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def register_request_id(app: Flask) -> None:
    @app.before_request
    def _assign_request_id() -> None:
        # Accept the caller's id so a trace survives across services; generate
        # one otherwise. Never trust it for anything but correlation.
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        g.request_id = incoming[:200] if incoming else uuid.uuid4().hex

    @app.after_request
    def _echo_request_id(response: Response) -> Response:
        if (request_id := g.get("request_id")) is not None:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response
