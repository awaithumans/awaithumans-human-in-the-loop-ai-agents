"""Shared fixtures for demo service tests.

Mirrors `tests/services/conftest.py`: in-memory async SQLite per test
with all models registered so `metadata.create_all` builds the
`demo_records` table. Named `db_session` to match the rate-limit
test contract.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

# Register the models so create_all picks up the tables. DemoRecord
# is the one this test suite cares about; the others are imported so
# the metadata is complete and any cross-table FK declarations resolve.
from awaithumans.server.db.models import (  # noqa: F401
    AuditEntry,
    DemoRecord,
    Task,
    User,
)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
