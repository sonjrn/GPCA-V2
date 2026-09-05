"""The request-scoped session boundary.

session_scope is where every write in the application will commit or roll
back, so its contract is worth pinning down before anything depends on it.
"""

import pytest
from flask import Flask
from sqlalchemy.orm import Session

from app.extensions import SESSION_FACTORY_KEY, get_engine, session_scope


class _RecordingSession:
    """Stands in for a Session, recording the calls that matter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def recording(app: Flask) -> _RecordingSession:
    session = _RecordingSession()
    app.extensions[SESSION_FACTORY_KEY] = lambda: session
    return session


def test_a_clean_block_commits_then_closes(app: Flask, recording: _RecordingSession) -> None:
    with app.app_context(), session_scope() as session:
        assert session is recording
    assert recording.calls == ["commit", "close"]


def test_an_exception_rolls_back_and_still_closes(app: Flask, recording: _RecordingSession) -> None:
    """The half of the contract that matters: a raise must not commit."""
    with pytest.raises(ValueError, match="boom"), app.app_context(), session_scope():
        raise ValueError("boom")
    assert recording.calls == ["rollback", "close"]
    assert "commit" not in recording.calls


def test_the_exception_propagates_unchanged(app: Flask, recording: _RecordingSession) -> None:
    """Rolling back must not swallow the error that caused it."""

    class SpecificError(Exception):
        pass

    with pytest.raises(SpecificError), app.app_context(), session_scope():
        raise SpecificError
    assert recording.calls == ["rollback", "close"]


def test_engine_is_per_application(app: Flask) -> None:
    """Stored on the app, not a module global, so tests do not share a pool."""
    with app.app_context():
        assert get_engine() is app.extensions["gpca_engine"]

    factory = app.extensions[SESSION_FACTORY_KEY]
    assert isinstance(factory(), Session)
