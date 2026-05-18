"""Service-layer tests for service_api_keys CRUD + verification.

Each test gets a fresh in-memory async SQLite engine + AsyncSession,
no module-level state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.services.exceptions import ServiceKeyNotFoundError
from awaithumans.server.services.service_key_service import (
    create_service_key,
    list_service_keys,
    revoke_service_key,
    verify_service_key,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Per-test in-memory async SQLite session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_create_returns_raw_key_once(session: AsyncSession) -> None:
    raw, row = await create_service_key(session, name="acme-prod")
    assert raw.startswith("ah_sk_")
    assert len(raw) > 20
    assert row.key_hash != raw
    assert row.key_prefix == raw[:12]
    assert row.name == "acme-prod"


async def test_verify_round_trip_succeeds(session: AsyncSession) -> None:
    raw, _ = await create_service_key(session, name="acme-prod")
    found = await verify_service_key(session, raw)
    assert found.name == "acme-prod"


async def test_verify_rejects_unknown_key(session: AsyncSession) -> None:
    with pytest.raises(ServiceKeyNotFoundError):
        await verify_service_key(session, "ah_sk_doesnotexist")


async def test_verify_rejects_revoked_key(session: AsyncSession) -> None:
    raw, row = await create_service_key(session, name="acme-prod")
    await revoke_service_key(session, row.id)
    with pytest.raises(ServiceKeyNotFoundError):
        await verify_service_key(session, raw)


async def test_list_excludes_revoked_by_default(session: AsyncSession) -> None:
    _, alpha = await create_service_key(session, name="alpha")
    _, beta = await create_service_key(session, name="beta")
    await revoke_service_key(session, alpha.id)
    listed = await list_service_keys(session, include_revoked=False)
    assert [k.id for k in listed] == [beta.id]


async def test_list_includes_revoked_when_requested(session: AsyncSession) -> None:
    _, alpha = await create_service_key(session, name="alpha")
    await revoke_service_key(session, alpha.id)
    listed = await list_service_keys(session, include_revoked=True)
    assert any(k.id == alpha.id for k in listed)


async def test_create_rejects_oversize_name(session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await create_service_key(session, name="x" * 81)
