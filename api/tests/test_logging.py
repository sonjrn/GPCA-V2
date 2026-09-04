"""Structured logging."""

import json
import logging

from app.logging import JsonFormatter


def _format(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="listing published %s",
        args=("vom-hausberg",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_output_is_valid_json_with_the_expected_fields() -> None:
    payload = _format(_record())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "listing published vom-hausberg"
    assert payload["timestamp"]


def test_extra_fields_are_promoted_to_top_level_keys() -> None:
    """This is what makes request_id filterable in a log aggregator."""
    payload = _format(_record(request_id="abc123", user_id=7))
    assert payload["request_id"] == "abc123"
    assert payload["user_id"] == 7


def test_exceptions_are_included_as_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    payload = _format(record)
    assert "ValueError: boom" in str(payload["exception"])


def test_unserializable_values_do_not_break_the_line() -> None:
    """A log call must never raise; a dropped field beats a lost log."""
    payload = _format(_record(weird=object()))
    assert "object object at" in str(payload["weird"])
