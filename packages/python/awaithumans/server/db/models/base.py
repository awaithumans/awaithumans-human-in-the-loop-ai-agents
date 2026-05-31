"""Shared utilities for database models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime


def utc_now() -> datetime:
    """Current UTC timestamp for default field values."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Generate a new hex UUID for primary keys."""
    return uuid4().hex


def tz_timestamp_column(*, nullable: bool = False, index: bool = False) -> Column:  # type: ignore[type-arg]
    """Return a ``TIMESTAMP WITH TIME ZONE`` column declaration.

    Pairs with the service-layer convention of writing tz-aware
    ``datetime.now(timezone.utc)`` values. Without ``timezone=True``
    SQLModel produces a plain ``DateTime``, which lands as
    ``TIMESTAMP WITHOUT TIME ZONE`` on Postgres. asyncpg then refuses
    to bind a tz-aware Python datetime to that column, and any query
    that does ``WHERE column <= now`` blows up with
    ``can't subtract offset-naive and offset-aware datetimes`` — the
    failure mode the timeout and webhook schedulers hit in production
    after the reviewer moved to Azure Postgres.

    SQLite ignores the ``timezone=True`` hint at the storage layer
    (datetimes round-trip as naive TEXT), so callers that mix code
    paths between SQLite and Postgres should keep using
    ``_ensure_utc(...)`` patterns on read. The point of this helper
    is to make the *column declaration* correct so:

    1. ``metadata.create_all`` (tests) produces tz-aware columns.
    2. Future alembic autogenerate runs emit ``timezone=True``.
    3. Existing Postgres deployments with naive columns get repaired
       at boot by ``_patch_naive_timestamp_columns`` in
       ``db/connection.py`` — it runs after alembic upgrade and
       ALTERs any column still typed ``TIMESTAMP WITHOUT TIME ZONE``.

    Each Field needs its own Column instance — don't cache the
    return value or share it across fields, since SQLAlchemy mutates
    Column state when binding to a Table.
    """
    return Column(DateTime(timezone=True), nullable=nullable, index=index)
