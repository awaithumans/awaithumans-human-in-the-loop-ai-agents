"""Datetime columns must be tz-aware on Postgres.

Background: pre-PR-6 OSS migrations declared datetime columns as bare
``sa.DateTime()``, which lands as ``TIMESTAMP WITHOUT TIME ZONE`` on
Postgres. The service layer always wrote tz-aware values
(``datetime.now(timezone.utc)``), so asyncpg refused to bind them as
parameters — every query that compared or inserted a datetime against
those columns crashed. The visible breakage was the timeout/webhook
schedulers ticking on errors and the operator-bootstrap
``POST /api/setup/complete`` returning 500, making the reviewer
dashboard unusable on Postgres.

The fix has three parts:

1. Model declarations use ``tz_timestamp_column()`` so new tables
   (via ``metadata.create_all`` in tests and brand-new deploys) get
   the right column type natively.
2. A startup helper, ``_patch_naive_timestamp_columns``, ALTERs
   pre-existing Postgres columns to ``TIMESTAMP WITH TIME ZONE``
   after alembic upgrade. Idempotent and a no-op on SQLite.
3. The patch list (``_NAIVE_TIMESTAMP_COLUMNS``) is kept in sync
   with the metadata via a test in this file — so adding a new
   tz-aware column to a model without updating the patch list
   trips a clear failure here, not silent prod drift.

These tests don't spin up a real Postgres (no testcontainers dep);
they verify the schema-introspection contract and the patch-list
coverage. End-to-end Postgres verification is the deploy step.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import DateTime
from sqlmodel import SQLModel

from awaithumans.server.db import models as _models  # noqa: F401  — import for table registration
from awaithumans.server.db.connection import (
    _NAIVE_TIMESTAMP_COLUMNS,
    _alter_naive_timestamps,
    _patch_naive_timestamp_columns,
)
from awaithumans.server.db.models.base import tz_timestamp_column

# Columns already declared tz-aware in their *original* alembic migration —
# i.e., they never landed as naive on Postgres, so the runtime patch
# doesn't need to repair them. Today this is service_api_keys (created
# in 20260508_0510 with explicit `sa.DateTime(timezone=True)`). Update
# this set if a future migration adds another such column.
_ALREADY_TZ_AWARE_IN_MIGRATION: frozenset[tuple[str, str]] = frozenset(
    {
        ("service_api_keys", "created_at"),
        ("service_api_keys", "last_used_at"),
        ("service_api_keys", "revoked_at"),
    }
)


def _tz_aware_columns_in_metadata() -> set[tuple[str, str]]:
    """Walk SQLModel metadata and return every DateTime(timezone=True)
    column as a (table, column) pair."""
    found: set[tuple[str, str]] = set()
    for table_name, table in SQLModel.metadata.tables.items():
        for col in table.columns:
            if isinstance(col.type, DateTime) and col.type.timezone:
                found.add((table_name, col.name))
    return found


def test_helper_emits_tz_aware_column() -> None:
    """`tz_timestamp_column()` returns a `DateTime(timezone=True)` Column.

    Locking the contract because every datetime field downstream
    relies on it. A regression here silently produces naive columns
    in every new table without anyone noticing until the schedulers
    crash on the next prod boot.
    """
    col = tz_timestamp_column()
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is False
    assert col.index is False

    nullable_col = tz_timestamp_column(nullable=True)
    assert nullable_col.nullable is True

    indexed_col = tz_timestamp_column(nullable=True, index=True)
    assert indexed_col.nullable is True
    assert indexed_col.index is True


def test_all_model_datetime_columns_are_tz_aware() -> None:
    """Every datetime column in the SQLModel metadata is tz-aware.

    If someone adds a model column with bare ``Field(default_factory
    =utc_now)`` instead of ``sa_column=tz_timestamp_column(...)``,
    they'll get a plain ``DateTime`` in metadata and the scheduler-
    crash bug returns on the next Postgres deploy. This test pins
    the contract: any DateTime in metadata MUST be ``timezone=True``.
    """
    naive_in_metadata: list[tuple[str, str]] = []
    for table_name, table in SQLModel.metadata.tables.items():
        for col in table.columns:
            if isinstance(col.type, DateTime) and not col.type.timezone:
                naive_in_metadata.append((table_name, col.name))

    assert not naive_in_metadata, (
        "Found naive datetime columns in SQLModel metadata. Every "
        "datetime field must use sa_column=tz_timestamp_column(...) "
        f"from server/db/models/base.py. Naive columns: {naive_in_metadata!r}"
    )


def test_patch_list_covers_every_tz_aware_column() -> None:
    """Every tz-aware column in metadata is either in the patch list or
    explicitly known to have been tz-aware from its original migration.

    This is the test that catches drift: someone adds a new datetime
    column with ``tz_timestamp_column()``, the alembic autogen
    creates a `TIMESTAMP WITH TIME ZONE` column (good!), but pre-PR-6
    Postgres deployments never get the chance to ALTER an existing
    naive version because the column wasn't there. So actually a new
    column doesn't need a patch entry. BUT if the column already
    exists in a pre-PR-6 migration as naive, and the model gets
    updated without adding it to `_NAIVE_TIMESTAMP_COLUMNS`, prod
    silently stays broken.

    The test enforces the strict invariant: the patch list is
    exhaustive for the moment of this PR. If a future PR adds a tz
    column to an existing table without a new migration (or with a
    migration that adds it as tz-aware), update
    ``_ALREADY_TZ_AWARE_IN_MIGRATION`` here too.
    """
    metadata_tz_cols = _tz_aware_columns_in_metadata()
    patch_set = set(_NAIVE_TIMESTAMP_COLUMNS)

    # Every tz-aware column should be either patched at boot or
    # already-correct-in-migration.
    uncovered = metadata_tz_cols - patch_set - _ALREADY_TZ_AWARE_IN_MIGRATION
    assert not uncovered, (
        "Tz-aware columns in models that aren't in _NAIVE_TIMESTAMP_COLUMNS "
        "and aren't in the known-already-correct set. Either add them to "
        f"_NAIVE_TIMESTAMP_COLUMNS in db/connection.py, or to "
        f"_ALREADY_TZ_AWARE_IN_MIGRATION in this test file if their "
        f"original migration already declared timezone=True: {sorted(uncovered)!r}"
    )

    # And the patch list shouldn't reference columns that aren't in
    # the model — would mean stale entries from a deleted column.
    extra = patch_set - metadata_tz_cols
    assert not extra, (
        "_NAIVE_TIMESTAMP_COLUMNS references columns that no longer exist "
        f"in any model. Remove these stale entries: {sorted(extra)!r}"
    )


async def test_patch_is_noop_on_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outer ``_patch_naive_timestamp_columns`` early-exits on a
    SQLite URL.

    The patch's SQL ( ``information_schema.columns``,
    ``TIMESTAMP WITH TIME ZONE`` ) is Postgres-specific. SQLite would
    raise on the first query. Tests, dev mode, and any non-Postgres
    self-host depend on this early return.

    Patches ``get_async_engine`` to a sentinel that would fail the
    test if invoked — proving the function bailed before touching
    any DB machinery. ``database_url_async`` is a Settings property,
    so we mock it at the module level rather than try to set it on
    the singleton.
    """
    sqlite_url = "sqlite+aiosqlite:///./test-only.db"

    # Stub the property to return SQLite. patch.object on a class
    # property is the supported pattern; Settings.database_url_async
    # is a @property so we replace the descriptor on the class.
    from awaithumans.server.core.config import Settings

    explosive_engine = MagicMock(
        side_effect=AssertionError("engine should not be touched on SQLite")
    )
    monkeypatch.setattr(
        "awaithumans.server.db.connection.get_async_engine",
        explosive_engine,
    )

    with patch.object(
        Settings,
        "database_url_async",
        new_callable=lambda: property(lambda self: sqlite_url),
    ):
        await _patch_naive_timestamp_columns()

    explosive_engine.assert_not_called()


async def test_patch_short_circuits_for_each_non_postgres_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: the URL prefix check has to match every
    non-Postgres scheme we might see, not just sqlite. If a future
    helper produces e.g. an unparseable URL, the patch should still
    return early rather than crash trying to connect.

    We don't actually run against every scheme — just verify the
    bare URL check rejects anything that isn't `postgresql://` or
    `postgresql+driver://`.
    """
    from awaithumans.server.core.config import Settings

    explosive_engine = MagicMock(side_effect=AssertionError("engine touched"))
    monkeypatch.setattr(
        "awaithumans.server.db.connection.get_async_engine",
        explosive_engine,
    )

    for url in (
        "sqlite:///./x.db",
        "sqlite+aiosqlite:///./x.db",
        "mysql://u:p@h:3306/db",
        "",
    ):
        with patch.object(
            Settings,
            "database_url_async",
            new_callable=lambda u=url: property(lambda self: u),
        ):
            await _patch_naive_timestamp_columns()

    explosive_engine.assert_not_called()


async def test_alter_emits_correct_sql_for_naive_column() -> None:
    """When a column is naive, the helper issues exactly the right
    ALTER TABLE statement against the connection.

    This is the load-bearing assertion for the production fix: the
    SQL has to (a) ALTER the column to TIMESTAMP WITH TIME ZONE,
    (b) preserve existing values via ``AT TIME ZONE 'UTC'`` so the
    column gets labeled, not shifted, and (c) target the right
    table/column. The test uses an AsyncMock connection so we can
    inspect the SQL without a real Postgres.
    """
    # The mocked execute returns "timestamp without time zone" for the
    # SELECT, simulating a naive column in Postgres' information_schema.
    naive_result = MagicMock()
    naive_result.scalar_one_or_none = MagicMock(return_value="timestamp without time zone")

    # Each call to conn.execute returns the same result regardless of
    # what was passed. That's fine — the SELECT and ALTER paths use
    # the same mock, and we only assert on `scalar_one_or_none` for
    # the SELECT.
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=naive_result)

    columns = (("tasks", "timeout_at"),)
    altered = await _alter_naive_timestamps(mock_conn, columns)

    assert altered == 1, "Expected exactly one column to be altered"
    assert mock_conn.execute.call_count == 2, (
        f"Expected SELECT then ALTER (2 calls); got: {mock_conn.execute.call_count}"
    )

    select_sql = str(mock_conn.execute.call_args_list[0].args[0])
    assert "information_schema.columns" in select_sql
    assert "table_name = :t" in select_sql
    assert "column_name = :c" in select_sql

    alter_sql = str(mock_conn.execute.call_args_list[1].args[0])
    assert 'ALTER TABLE "tasks"' in alter_sql
    assert 'ALTER COLUMN "timeout_at"' in alter_sql
    assert "TYPE TIMESTAMP WITH TIME ZONE" in alter_sql
    assert "AT TIME ZONE 'UTC'" in alter_sql


async def test_alter_skips_already_tz_aware_columns() -> None:
    """Idempotency check: re-running the patch is free.

    The whole point of running this on every boot is that
    self-healing must be cheap when there's nothing to heal. If the
    SELECT returns ``timestamp with time zone``, no ALTER should
    fire and ``altered`` should be 0. A regression here would mean
    every server boot re-rewrites every datetime column on every
    table — fine on tiny tables, catastrophic at scale.
    """
    aware_result = MagicMock()
    aware_result.scalar_one_or_none = MagicMock(return_value="timestamp with time zone")

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=aware_result)

    columns = (("tasks", "timeout_at"), ("users", "created_at"))
    altered = await _alter_naive_timestamps(mock_conn, columns)

    assert altered == 0
    # Only SELECT per column, no ALTER.
    assert mock_conn.execute.call_count == 2, "Expected exactly one SELECT per column, no ALTERs"


async def test_alter_skips_missing_columns() -> None:
    """If ``information_schema`` returns no row (column was renamed
    or dropped, or alembic hasn't created the table yet for some
    reason), the helper skips that entry cleanly rather than
    crashing.

    Alembic owns column lifecycle. This patch is *only* a type
    repair; it must never error on a missing column or it'd block
    boot for a deployment that's mid-migration.
    """
    missing_result = MagicMock()
    missing_result.scalar_one_or_none = MagicMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=missing_result)

    altered = await _alter_naive_timestamps(
        mock_conn,
        (("tasks", "nonexistent_column"),),
    )

    assert altered == 0
    assert mock_conn.execute.call_count == 1  # only the SELECT, no ALTER


async def test_alter_mixed_naive_and_aware_columns() -> None:
    """A realistic prod scenario: some columns are still naive (need
    repair), some have already been altered on a prior boot. The
    helper should ALTER only the naive ones and leave the aware
    ones alone.
    """
    naive_result = MagicMock()
    naive_result.scalar_one_or_none = MagicMock(return_value="timestamp without time zone")
    aware_result = MagicMock()
    aware_result.scalar_one_or_none = MagicMock(return_value="timestamp with time zone")

    # First column: naive (SELECT then ALTER = 2 calls)
    # Second column: aware (SELECT only = 1 call)
    # Total: 3 calls. The execute mock returns naive_result first,
    # naive_result for ALTER (irrelevant), then aware_result.
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(side_effect=[naive_result, naive_result, aware_result])

    columns = (("tasks", "timeout_at"), ("tasks", "created_at"))
    altered = await _alter_naive_timestamps(mock_conn, columns)

    assert altered == 1
    assert mock_conn.execute.call_count == 3


def test_patch_list_matches_pre_pr6_schema_exactly() -> None:
    """Pin the exact contents of the patch list so a reviewer can
    glance at this test and verify the (table, column) pairs match
    the audit done in PR 6's design notes. Removing an entry without
    confirming the corresponding column was migrated tz-aware first
    is a hazard worth a test.
    """
    expected = {
        ("audit_entries", "created_at"),
        ("consumed_email_tokens", "consumed_at"),
        ("email_sender_identities", "verified_at"),
        ("email_sender_identities", "created_at"),
        ("email_sender_identities", "updated_at"),
        ("slack_installations", "installed_at"),
        ("slack_installations", "updated_at"),
        ("slack_task_messages", "created_at"),
        ("tasks", "created_at"),
        ("tasks", "updated_at"),
        ("tasks", "completed_at"),
        ("tasks", "timed_out_at"),
        ("tasks", "timeout_at"),
        ("users", "last_assigned_at"),
        ("users", "created_at"),
        ("users", "updated_at"),
        ("webhook_deliveries", "next_attempt_at"),
        ("webhook_deliveries", "first_attempted_at"),
        ("webhook_deliveries", "last_attempt_at"),
        ("webhook_deliveries", "created_at"),
        ("webhook_deliveries", "updated_at"),
    }
    assert set(_NAIVE_TIMESTAMP_COLUMNS) == expected, (
        "Patch list drifted from PR 6's documented audit. If this is "
        "intentional (e.g., a new alembic migration created a column "
        "tz-aware from the start), update _ALREADY_TZ_AWARE_IN_MIGRATION "
        "above too."
    )
