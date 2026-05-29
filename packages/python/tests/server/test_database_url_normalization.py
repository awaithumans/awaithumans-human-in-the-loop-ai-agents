"""Postgres URL SSL parameter normalization.

When operators deploy against a managed Postgres provider (Azure,
Heroku, Supabase, Render, etc.) the provider hands them a URL with
``?sslmode=require``. Without translation:

- The async engine uses asyncpg which doesn't recognize ``sslmode``
  as a parameter NAME (it accepts the same VALUES under ``ssl=``).
- The sync engine uses psycopg which doesn't recognize ``ssl=``.

We translate in both directions so the same DATABASE_URL works for
both the runtime engine AND alembic migrations.

These are unit tests against the Settings property rather than full
DB connections — verifying the wire format, not Postgres behavior.
"""

from __future__ import annotations

import pytest

from awaithumans.server.core.config import Settings


@pytest.mark.parametrize(
    "value",
    ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
)
def test_async_translates_libpq_sslmode_to_asyncpg_ssl(value: str) -> None:
    """Operator-pasted libpq URL works on asyncpg without edits."""
    s = Settings(
        DATABASE_URL=f"postgresql://user:pw@host:5432/db?sslmode={value}"
    )
    assert s.database_url_async == (
        f"postgresql+asyncpg://user:pw@host:5432/db?ssl={value}"
    )


@pytest.mark.parametrize(
    "value",
    ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
)
def test_sync_translates_asyncpg_ssl_to_libpq_sslmode(value: str) -> None:
    """Operator pasted an asyncpg-style URL — alembic still works."""
    s = Settings(
        DATABASE_URL=f"postgresql://user:pw@host:5432/db?ssl={value}"
    )
    assert s.database_url_sync == (
        f"postgresql://user:pw@host:5432/db?sslmode={value}"
    )


def test_async_handles_heroku_postgres_scheme() -> None:
    """Heroku still hands out ``postgres://``; we accept it as
    a synonym for ``postgresql://``."""
    s = Settings(DATABASE_URL="postgres://user:pw@host:5432/db?sslmode=require")
    assert s.database_url_async == (
        "postgresql+asyncpg://user:pw@host:5432/db?ssl=require"
    )


def test_async_preserves_url_without_ssl_param() -> None:
    """No SSL param → no translation, just driver rewrite."""
    s = Settings(DATABASE_URL="postgresql://user:pw@host:5432/db")
    assert s.database_url_async == "postgresql+asyncpg://user:pw@host:5432/db"


def test_sync_preserves_url_without_ssl_param() -> None:
    s = Settings(DATABASE_URL="postgresql://user:pw@host:5432/db")
    assert s.database_url_sync == "postgresql://user:pw@host:5432/db"


def test_async_passes_through_already_async_url() -> None:
    """``postgresql+asyncpg://`` URLs land unchanged on the driver
    rewrite step; the SSL translation still runs."""
    s = Settings(
        DATABASE_URL="postgresql+asyncpg://user:pw@host:5432/db?sslmode=require"
    )
    assert s.database_url_async == (
        "postgresql+asyncpg://user:pw@host:5432/db?ssl=require"
    )


def test_sync_translation_with_multiple_query_params() -> None:
    """Operators sometimes pass extra params alongside sslmode
    (application_name, connect_timeout, etc.). Only ssl= gets
    rewritten."""
    s = Settings(
        DATABASE_URL=(
            "postgresql://user:pw@host:5432/db?ssl=require"
            "&application_name=alembic"
        )
    )
    assert s.database_url_sync == (
        "postgresql://user:pw@host:5432/db?sslmode=require"
        "&application_name=alembic"
    )


def test_sqlite_url_is_unaffected() -> None:
    """SQLite URLs don't have SSL semantics; the helpers shouldn't
    touch them."""
    # The helper functions are pure string ops; SQLite goes through
    # a separate fallback in the property. Confirm that fallback.
    s = Settings(DATABASE_URL=None, DB_PATH="/tmp/test.db")
    assert s.database_url_async == "sqlite+aiosqlite:////tmp/test.db"
    assert s.database_url_sync == "sqlite:////tmp/test.db"
