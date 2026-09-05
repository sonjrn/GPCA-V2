"""The db/ layer must not import the web layer.

This is enforced by db/ruff.toml (TID251), and asserted here so a broken rule
fails an ordinary test run rather than only being noticed in review. The
packaging split makes the dependency impossible in one direction; this covers
the other, where nothing stops an author typing `import flask`.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import gpca_db

PACKAGE_ROOT = Path(gpca_db.__file__).parent

BANNED = ["flask", "pydantic", "pydantic_settings", "stripe", "boto3", "app"]


def _ruff_check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--select", "TID", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def probe_module() -> Path:
    """A real file inside db/, because ruff picks config by file location.

    A temp file elsewhere would be checked against the root config and would
    not exercise db/ruff.toml at all.
    """
    path = PACKAGE_ROOT / f"_boundary_probe_{os.getpid()}.py"
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.parametrize("module", BANNED)
def test_banned_import_fails_lint(probe_module: Path, module: str) -> None:
    probe_module.write_text(f"import {module}\n")
    result = _ruff_check(probe_module)
    assert result.returncode != 0, f"`import {module}` was allowed under db/"
    assert "TID251" in result.stdout


def test_an_allowed_import_passes(probe_module: Path) -> None:
    """The rule must ban the web layer, not everything."""
    probe_module.write_text("import sqlalchemy\n")
    result = _ruff_check(probe_module)
    assert result.returncode == 0, result.stdout
