"""drop orphaned demo_records table

Revision ID: 4c51f4e2d8f9
Revises: 8b4ed1c70a5f
Create Date: 2026-06-22 05:30:00.000000

Cleanup migration: the ``demo_records`` table (and its
``demostatus`` ENUM) shipped briefly in PR #184 and were removed
from the OSS application code in PR #186 (the demo backend moved
to the private managed-service repo). The Alembic migration files
that created them were deleted along with the application code,
which left every existing OSS Postgres deployment in a state where:

  * The application code no longer imports ``DemoRecord`` /
    ``DemoStatus`` (the SQLModel definitions are gone), so no code
    path reads or writes the table.
  * ``alembic_version`` was stamped at ``20260606_2000_demo_records
    _dynamic_schema``, which alembic could no longer locate in
    ``versions/`` (file was deleted by PR #186).
  * Every container start ran ``alembic upgrade head``, alembic
    refused to start with ``Can't locate revision identified by
    '20260606_2000'``, and the reviewers Container App
    crash-looped.

The interim fix on 2026-06-22 was a manual ``UPDATE
alembic_version SET version_num = '8b4ed1c70a5f'`` against the
prod OSS DB, rolling the stamp back to the last pre-demo
migration. That unblocked startup but left the ``demo_records``
table and ``demostatus`` ENUM physically present in the DB,
referenced by nothing.

This migration tidies up: drops the orphaned table and ENUM, then
becomes the new head so the alembic history catches up with the
application code.

Downgrade is intentionally a no-op. We never want to recreate the
demo_records table on the OSS side — the demo backend lives in
the managed repo now and owns its own (separate) database. A
proper restore path would mean re-introducing the model classes
and routes, which is out of scope for any sensible rollback of
this commit alone.
"""

from __future__ import annotations

from typing import Any

from alembic import op

revision: str = "4c51f4e2d8f9"
down_revision: str | None = "8b4ed1c70a5f"
branch_labels: Any = None
depends_on: Any = None


def upgrade() -> None:
    """Drop the orphaned demo_records table and its ENUM type.

    Wrapped in IF EXISTS so the upgrade is idempotent: a fresh OSS
    deployment that was provisioned post-PR-186 never had the table
    in the first place, and this migration should still apply
    cleanly on those.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.execute("DROP TABLE IF EXISTS demo_records")

    # The ENUM type is Postgres-specific; SQLite stores enums as
    # CHECK constraints inline with the column and there's nothing
    # left to clean up once the table is gone.
    if dialect == "postgresql":
        op.execute("DROP TYPE IF EXISTS demostatus")


def downgrade() -> None:
    """Intentional no-op. The OSS application no longer ships demo
    routes or models; recreating the table without the code that
    populates it would only return the DB to the inconsistent
    state this migration cleans up.
    """
    # Use a SQL comment so alembic still emits an executable
    # statement (some pgbouncer pools error on empty transactions).
    op.execute("-- drop_orphaned_demo_records: downgrade is a no-op")
