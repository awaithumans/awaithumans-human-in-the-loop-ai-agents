"""Database connection management.

Uses the centralized settings from server/core/config.py.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config as AlembicConfig
from awaithumans.server.core.config import settings

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
    """
    import asyncio

    def _run() -> None:
        command.upgrade(_alembic_config(), "head")

    await asyncio.to_thread(_run)
    logger.info("Database migrations applied (head)")


async def close_db() -> None:
    """Close the database engine. Called on server shutdown."""
    global _async_engine, _async_session_factory
    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None
        logger.info("Database engine closed")
