"""Database connection management.

Uses the centralized settings from server/core/config.py.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config as AlembicConfig
from awaithumans.server.core.config import settings

# Columns that pre-PR-6 alembic migrations created as
# `TIMESTAMP WITHOUT TIME ZONE`. On Postgres asyncpg refuses to bind
# tz-aware Python datetimes against these, which broke the timeout
# and webhook schedulers in production. The startup patch below ALTERs
# each one to `TIMESTAMP WITH TIME ZONE`, interpreting existing values
# as UTC (which the service layer's `datetime.now(timezone.utc)` write
# path always was). Listed (table, column) pairs only — never used as
# a positive allowlist, always cross-checked against
# information_schema so dropped or renamed columns no-op cleanly.
#
# Already tz-aware in their original migration (skipped):
#   service_api_keys.{created_at,last_used_at,revoked_at} — 20260508_0510
_NAIVE_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
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
)

logger = logging.getLogger("awaithumans.server.db")

# Async engine (lazily initialized)
_async_engine = None

# Async session factory (lazily initialized)
_async_session_factory = None


def get_async_engine():
    """Get or create the async database engine."""
    global _async_engine
    if _async_engine is None:
        url = settings.database_url_async
        connect_args = {}
        if "sqlite" in url:
            connect_args["check_same_thread"] = False
        _async_engine = create_async_engine(url, connect_args=connect_args, echo=False)
        logger.info("Database engine created (url=%s)", url.split("@")[-1] if "@" in url else url)
    return _async_engine


def get_async_session_factory():
    """Get or create the async session factory."""
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_async_engine()
        _async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async database session."""
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


def _alembic_paths() -> tuple[Path, Path]:
    """Return (alembic_ini, alembic_script_dir) for the current install.

    Two layouts are supported:
    - Dev checkout: `packages/python/alembic.ini` alongside `awaithumans/`.
    - Installed wheel: bundled at `awaithumans/_alembic/alembic.ini` via
      hatchling's `force-include` (see pyproject.toml).

    Check dev first so `pip install -e .` and direct `alembic` CLI use in
    the monorepo resolve to the same files the wheel would ship.
    """
    dev_ini = Path(__file__).resolve().parents[3] / "alembic.ini"
    if dev_ini.exists():
        return dev_ini, dev_ini.parent / "alembic"

    pkg_root = Path(__file__).resolve().parents[2]
    bundled_ini = pkg_root / "_alembic" / "alembic.ini"
    return bundled_ini, pkg_root / "_alembic" / "alembic"


def _alembic_config() -> AlembicConfig:
    """Build an AlembicConfig with the URL and script_location pinned
    to the current install layout."""
    ini_path, script_dir = _alembic_paths()
    cfg = AlembicConfig(str(ini_path))
    cfg.set_main_option("script_location", str(script_dir))
    cfg.set_main_option("sqlalchemy.url", settings.database_url_sync)
    return cfg


async def init_db() -> None:
    """Run any pending migrations. Called on server startup.

    Alembic is sync; we run it in a thread so we don't block the loop.
    Tests bypass init_db and use SQLModel.metadata.create_all directly
    against their own in-memory engines.

    After alembic, runs `_patch_naive_timestamp_columns()` to repair
    Postgres deployments whose pre-PR-6 migrations created datetime
    columns without `TIMESTAMP WITH TIME ZONE`. Idempotent and a no-op
    on SQLite — see the docstring on that function for the why.
    """
    import asyncio

    def _run() -> None:
        command.upgrade(_alembic_config(), "head")

    await asyncio.to_thread(_run)
    logger.info("Database migrations applied (head)")

    await _patch_naive_timestamp_columns()


async def _patch_naive_timestamp_columns() -> None:
    """Promote pre-PR-6 naive timestamp columns to ``TIMESTAMP WITH TIME ZONE``.

    Background: the OSS server's service layer always wrote tz-aware
    datetimes (``datetime.now(timezone.utc)``), but the original alembic
    migrations declared ``sa.DateTime()`` without ``timezone=True``. On
    SQLite this is invisible — SQLite has no real datetime type and
    accepts the values as text. On Postgres each column lands as
    ``TIMESTAMP WITHOUT TIME ZONE``, and asyncpg refuses to bind a
    tz-aware Python datetime against it. The timeout scheduler's
    ``WHERE timeout_at <= now`` query (and the webhook scheduler's
    equivalent) crashes every tick.

    The fix has three parts: (1) model declarations updated to use
    ``tz_timestamp_column`` so new tables and tests are correct,
    (2) this runtime patch to ALTER existing prod columns to match,
    (3) the service layer was already correct.

    Why a runtime patch instead of a new alembic revision: adding an
    alembic migration that ALTERs columns on every prod database is
    deploy-coupling we don't need. The patch is idempotent (skips
    columns whose ``data_type`` is already ``timestamp with time
    zone``), so booting an already-patched server is free. New
    deployments run the original migrations, hit the patch once on
    first boot, and stay correct after that. Once every running
    deployment has booted under PR 6, the table contents are stable
    and this function can be deleted.

    SQLite is skipped entirely: ``information_schema`` doesn't exist,
    ``timestamp with time zone`` is meaningless, and the bug never
    manifested there in the first place.
    """
    url = settings.database_url_async
    if not url.startswith(("postgresql://", "postgresql+")):
        return

    engine = get_async_engine()
    async with engine.begin() as conn:
        await _alter_naive_timestamps(conn, _NAIVE_TIMESTAMP_COLUMNS)


async def _alter_naive_timestamps(
    conn: object,  # AsyncConnection — typed loosely so tests can pass an AsyncMock
    columns: tuple[tuple[str, str], ...],
) -> int:
    """Inner half of the patch — ALTER each naive column to tz-aware.

    Split from ``_patch_naive_timestamp_columns`` so tests can drive
    it with a mocked connection (the outer function's
    ``engine.begin()`` async-context-manager is awkward to mock).
    Returns the number of columns actually altered, which lets the
    test assert idempotency: a second call against an already-patched
    schema returns 0.
    """
    altered = 0
    for table, column in columns:
        current_type = (
            await conn.execute(  # type: ignore[attr-defined]
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            )
        ).scalar_one_or_none()

        # None: column doesn't exist (table not yet created on a
        # fresh deploy where this runs before alembic finishes, or
        # the column was renamed/dropped). Skip — alembic owns the
        # column lifecycle, this patch is only here to repair type.
        if current_type is None:
            continue

        # Already tz-aware: either alembic created it correctly
        # (service_api_keys) or this patch ran on a previous boot.
        if current_type != "timestamp without time zone":
            continue

        # AT TIME ZONE 'UTC' attaches a tz to the existing naive
        # values. The service layer always wrote UTC by
        # construction, so this is value-preserving — we're
        # labeling the data with the timezone it already had,
        # not converting it. Quoted identifiers because Postgres
        # otherwise lowercases unquoted names; column names in
        # this codebase happen to be lowercase already, but
        # quoting is defensive against future renames.
        await conn.execute(  # type: ignore[attr-defined]
            text(
                f'ALTER TABLE "{table}" '
                f'ALTER COLUMN "{column}" TYPE TIMESTAMP WITH TIME ZONE '
                f"USING \"{column}\" AT TIME ZONE 'UTC'"
            )
        )
        altered += 1
        logger.info(
            "Promoted %s.%s to TIMESTAMP WITH TIME ZONE (UTC-anchored)",
            table,
            column,
        )
    return altered


async def close_db() -> None:
    """Close the database engine. Called on server shutdown."""
    global _async_engine, _async_session_factory
    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None
        logger.info("Database engine closed")
