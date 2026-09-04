"""GPCA persistence layer.

Holds the SQLAlchemy models, the queries that load them, and the Alembic
migrations that create them -- and nothing else. No business rules, no
authorization, no HTTP. See docs/technical-design.md section 12.2.
"""

__version__ = "0.1.0"
