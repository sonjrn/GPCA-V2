"""The generated DBML document.

The value of this file is that it cannot go stale, so the tests are mostly
about determinism and about the generator reflecting real metadata rather
than about the exact prose of the output.
"""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

from gpca_db import models  # noqa: F401 - populates Base.metadata
from gpca_db.base import Base
from gpca_db.schema_docs import render_dbml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "docs" / "schema.dbml"


def _probe_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "owners",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
    )
    Table(
        "kennels",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("owner_id", Integer, ForeignKey("owners.id"), nullable=False),
        Column("vet_id", Integer, ForeignKey("owners.id"), nullable=True),
    )
    return metadata


def test_tables_and_columns_are_emitted() -> None:
    document = render_dbml(_probe_metadata())
    assert "Table owners {" in document
    assert "  id integer [pk]" in document
    assert "  name varchar(50)" in document


def test_not_null_is_marked_and_nullable_is_not() -> None:
    document = render_dbml(_probe_metadata())
    assert "  owner_id integer [not null]" in document
    assert "  vet_id integer\n" in document


def test_foreign_keys_become_refs() -> None:
    """`>` is many-to-one, which is what a plain foreign key means."""
    document = render_dbml(_probe_metadata())
    assert "Ref: kennels.owner_id > owners.id" in document
    assert "Ref: kennels.vet_id > owners.id" in document


def test_output_is_deterministic() -> None:
    """Regenerating without a schema change must produce no diff.

    A generator that reorders on every run makes each schema review drown in
    noise, and reviewers stop reading the diff at all.
    """
    metadata = _probe_metadata()
    assert render_dbml(metadata) == render_dbml(metadata)


def test_tables_are_sorted_by_name() -> None:
    document = render_dbml(_probe_metadata())
    assert document.index("Table kennels") < document.index("Table owners")


def test_timestamps_use_the_short_postgres_spelling() -> None:
    """`timestamp with time zone` has spaces and would need quoting in DBML."""
    document = render_dbml(Base.metadata)
    assert "timestamptz" in document
    assert "timestamp with time zone" not in document


def test_generation_refuses_to_write_an_empty_document() -> None:
    """Guards the failure this test file found: metadata is empty unless
    gpca_db.models has been imported, and an empty document would look like a
    schema with no tables rather than a bug."""
    result = subprocess.run(
        [sys.executable, "-c", "from gpca_db.schema_docs import main; main()"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    # Importing main() pulls models in, so this must succeed and be non-empty.
    assert result.returncode == 0, result.stderr
    assert "Table users" in result.stdout


def test_banner_marks_the_file_generated_and_names_its_scope() -> None:
    document = render_dbml(Base.metadata)
    assert "DO NOT EDIT" in document
    assert "IMPLEMENTED" in document
    assert "technical-design.md" in document


def test_committed_file_matches_the_generator() -> None:
    """The same assertion CI makes, so a stale file fails locally too."""
    generated = subprocess.run(
        [sys.executable, "-m", "gpca_db.schema_docs"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout
    assert COMMITTED.read_text() == generated, (
        "docs/schema.dbml is stale. Regenerate with:\n"
        "  python -m gpca_db.schema_docs > docs/schema.dbml"
    )
