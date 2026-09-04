# GPCA-V2

Full-stack GPCA application to replace the current site.

The database and API design lives in
[`docs/technical-design.md`](docs/technical-design.md). Read it before adding
anything here -- it covers the schema, the endpoint surface, and the reasoning
behind the layout below.

## Layout

The repository is a monorepo of four layers plus docs. Dependencies point in
one direction only: a lower layer never imports a higher one.

```
ui/       browser client                                       (not yet designed)
   |  HTTP
api/      Flask routes - schemas - services - integrations - jobs
   |  imports
db/       SQLAlchemy models - enums - repositories - Alembic migrations
   |
infra/    Dockerfiles, compose, nginx, deploy config           (no application code)
```

| Directory | Contents |
| --- | --- |
| `ui/` | Frontend client. Placeholder. |
| `api/` | Service layer. Flask, wire schemas, business rules. Distribution `gpca-api`, package `app`. |
| `db/` | Persistence layer. Distribution `gpca-db`, package `gpca_db`. |
| `infra/` | Docker, Compose, nginx, environment templates. |
| `docs/` | Design documentation. |

### The one rule

`db/` must not import Flask, Pydantic, Stripe, boto3, or anything from `api/`.
It is the database layer -- models, queries and migrations -- not a domain
layer: no business rules live there.

This is enforced two ways, so it cannot erode quietly:

1. **Packaging.** `api` depends on `gpca-db`; `db` has no path back.
2. **Lint.** `db/ruff.toml` bans those imports outright, so a violation fails
   `ruff check` rather than waiting on review.

## Getting started

Requires Python 3.14+.

```bash
python -m venv .venv && source .venv/bin/activate

# Order matters: api declares gpca-db as a dependency.
pip install -e ./db
pip install -e "./api[dev]"

pre-commit install
```

Or with [uv](https://docs.astral.sh/uv/), which resolves the sibling package
from the working tree:

```bash
uv venv --python 3.14 && source .venv/bin/activate
uv pip install -e ./db -e "./api[dev]"
```

### Checks

```bash
ruff check .           # lint, including the db/ import boundary
ruff format --check .  # formatting
mypy                   # strict type checking, targets from mypy.ini
pre-commit run --all-files
```

### Configuration

Copy `infra/env/.env.example` to `.env` and fill it in. Every variable the
application reads is listed there, and config validation at boot means a
missing value fails startup rather than the first request that needs it.

## Running it

Docker and Compose land in #6, CI in #7. Until then this tree is a shell:
the packages install and the checks pass, but there is no application to run.
