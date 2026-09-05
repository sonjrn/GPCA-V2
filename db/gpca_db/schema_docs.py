"""Render the implemented schema as DBML.

DBML is dbdiagram.io's input format, so the generated file is pasted straight
in and inspected. Scope is deliberately tables and relations only: columns with
their types, primary keys, nullability, and the foreign keys between them.
Indexes and enum value lists are not emitted -- they belong to the migrations,
and a schema overview is easier to read without them.

The source is `Base.metadata`, not a live database, so this runs with nothing
started. That is only sound because `alembic check` in CI enforces that the
models match the migrations; without it this would document the models rather
than the schema.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect

__all__ = ["render_dbml"]

# Built once. SQLAlchemy's dialect factory is untyped, so it is isolated here
# rather than sprinkling ignores through the rendering code.
_PG_DIALECT = postgres_dialect()  # type: ignore[no-untyped-call]

# PostgreSQL spells these with spaces, which DBML would need quoted. The
# canonical short forms are unambiguous and read better in dbdiagram.
_TYPE_ALIASES = {
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "double precision": "float8",
    "character varying": "varchar",
}

_BANNER = """// ---------------------------------------------------------------------------
// GENERATED FILE -- DO NOT EDIT.
//
// The IMPLEMENTED schema: what the migrations have actually built.
// For the full intended design, most of which is not built yet, see
// section 5.1 of docs/technical-design.md.
//
// Regenerate with:
//     python -m gpca_db.schema_docs > docs/schema.dbml
//
// Paste into https://dbdiagram.io to view it.
//
// Tables and relations only -- indexes and enum values are not shown.
// ---------------------------------------------------------------------------"""


def _column_type(column: Column[object]) -> str:
    """The PostgreSQL type name, normalized for DBML."""
    rendered = column.type.compile(dialect=_PG_DIALECT).lower()
    for spelling, alias in _TYPE_ALIASES.items():
        if rendered.startswith(spelling):
            rendered = rendered.replace(spelling, alias, 1)
            break
    # Anything still carrying a space would break the column definition.
    return f'"{rendered}"' if " " in rendered else rendered


def _column_line(column: Column[object]) -> str:
    settings = []
    if column.primary_key:
        settings.append("pk")
    if not column.nullable and not column.primary_key:
        settings.append("not null")
    suffix = f" [{', '.join(settings)}]" if settings else ""
    return f"  {column.name} {_column_type(column)}{suffix}"


def _table_block(table: Table) -> str:
    # Definition order, not alphabetical: it groups related columns the way the
    # model does, and is just as deterministic.
    lines = [f"Table {table.name} {{"]
    lines += [_column_line(column) for column in table.columns]
    lines.append("}")
    return "\n".join(lines)


def _ref_lines(metadata: MetaData) -> list[str]:
    """One `Ref` per foreign key, child to parent.

    `>` is many-to-one: many rows in the referencing table point at one row in
    the referenced table, which is what a plain foreign key means.
    """
    refs = {
        f"Ref: {table.name}.{fk.parent.name} > {fk.column.table.name}.{fk.column.name}"
        for table in metadata.tables.values()
        for fk in table.foreign_keys
    }
    return sorted(refs)


def render_dbml(metadata: MetaData, *, revision: str | None = None) -> str:
    """Render `metadata` as a DBML document.

    Deterministic: tables sorted by name, columns in definition order, refs
    sorted. Regenerating without a schema change must produce no diff, or every
    schema review drowns in reordering noise.
    """
    header = [_BANNER]
    if revision:
        header.append(f"// Alembic head: {revision}")

    blocks = [
        _table_block(table)
        for _, table in sorted(metadata.tables.items(), key=lambda item: item[0])
    ]
    refs = _ref_lines(metadata)

    sections = ["\n".join(header), "\n\n".join(blocks)]
    if refs:
        sections.append("\n".join(refs))
    return "\n\n".join(sections) + "\n"


def _alembic_head() -> str | None:
    """The current head revision, read from the migration scripts.

    Best effort: the document is still correct without it, so a missing or
    unreadable alembic.ini must not stop generation.
    """
    from pathlib import Path

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parent.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:
        return None
    return ", ".join(heads) if heads else None


def main() -> None:
    from gpca_db import models  # noqa: F401 - imported so metadata is populated
    from gpca_db.base import Base

    if not Base.metadata.tables:
        # Without the import above, metadata is empty and this would happily
        # write a document describing no schema at all.
        raise SystemExit("No tables in Base.metadata -- gpca_db.models did not import.")

    print(render_dbml(Base.metadata, revision=_alembic_head()), end="")


if __name__ == "__main__":
    main()
