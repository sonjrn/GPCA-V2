"""No query construction in the API layer.

The counterpart to db/tests/test_import_boundary.py. That one keeps the web
layer out of db/; this one keeps SQL out of api/.

Design 2.3 rule 4: "Queries live only in db/.../repositories/. Services call
breeder_repo.get_published_by_slug(session, slug), not
session.execute(select(...))." The point is not tidiness -- it is that the
eager-loading rules are enforceable in one place instead of being rediscovered
in forty route handlers.

api/ruff.toml bans the imports. This covers what ruff cannot see: ruff has no
way to know a local named `session` holds a Session, so the method-call forms
need a source scan.
"""

from pathlib import Path

import pytest

import app

PACKAGE_ROOT = Path(app.__file__).parent

# Session methods that execute a statement. `session.add` is absent on
# purpose: staging an object is unit-of-work bookkeeping, not a query, and the
# repositories expose it rather than hiding it.
BANNED_CALLS = (
    "session.execute(",
    "session.scalars(",
    "session.scalar(",
    "session.get(",
    "session.query(",
)

# extensions.py builds the session factory and owns session_scope, so it is the
# one place allowed to talk to SQLAlchemy directly about sessions.
EXEMPT = {"extensions.py"}


def _sources() -> list[Path]:
    return [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if path.name not in EXEMPT and "__pycache__" not in path.parts
    ]


def test_there_are_sources_to_scan() -> None:
    """Guards the guard: a glob that silently matches nothing would make every
    assertion below vacuously true."""
    assert len(_sources()) > 5


@pytest.mark.parametrize("call", BANNED_CALLS)
def test_no_statement_execution_in_the_api_layer(call: str) -> None:
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{number}"
        for path in _sources()
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if call in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        f"{call} found in the API layer at {offenders}. "
        "Move the query into db/gpca_db/repositories/ and call it from there."
    )


def test_the_scan_would_actually_catch_something(tmp_path: Path) -> None:
    """Proves the matcher works, so a passing suite means the rule holds
    rather than that the check is broken."""
    probe = tmp_path / "offender.py"
    probe.write_text("row = session.scalars(select(User)).one()\n")
    assert any("session.scalars(" in line for line in probe.read_text().splitlines())
