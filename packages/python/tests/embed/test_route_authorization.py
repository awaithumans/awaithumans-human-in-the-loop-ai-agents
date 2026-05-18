"""Integration tests for embed bearer auth on /api/tasks/* routes (Task 13).

Inline fixtures — no dependency on Task 15 conftest.

Exercises four cases:
  1. GET /api/tasks/<own_task_id> with embed bearer → 200.
  2. GET /api/tasks/<other_task_id> with embed bearer → 403.
  3. POST /api/tasks/<own_task_id>/complete with embed bearer → 200.
  4. GET /api/tasks (list) with embed bearer → 401 or 403.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.core.config import settings
from awaithumans.server.core.embed_auth import EmbedAuthMiddleware
from awaithumans.server.core.exceptions import exception_handlers
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import Task
from awaithumans.server.db.models.base import utc_now
from awaithumans.server.routes import tasks as task_routes
from awaithumans.server.services.embed_token_service import sign_embed_token
from awaithumans.types import TaskStatus

# ── Constants ─────────────────────────────────────────────────────────────────

_SIGNING_SECRET = "x" * 32
_ALLOWED_ORIGIN = "https://acme.com"
_TASK_ID = "tsk_seeded"
_OTHER_TASK_ID = "tsk_other"
_EMBED_SUB = "acme:user_4271"


# ── In-memory async engine ────────────────────────────────────────────────────


def _make_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _setup_db(engine) -> None:
    """Create all tables and seed two Task rows."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for task_id in (_TASK_ID, _OTHER_TASK_ID):
            task = Task(
                id=task_id,
                idempotency_key=f"idem-{task_id}",
                task="Review this document",
                payload={},
                payload_schema={},
                response_schema={},
                timeout_seconds=3600,
                status=TaskStatus.CREATED,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(task)
        await session.commit()


def _build_app(engine) -> FastAPI:
    """Build a minimal app wired to the given engine with EmbedAuthMiddleware."""
    asyncio.new_event_loop().run_until_complete(_setup_db(engine))

    test_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with test_factory() as session:
            yield session

    app = FastAPI()
    for exc_class, handler in exception_handlers.items():
        app.add_exception_handler(exc_class, handler)

    # EmbedAuthMiddleware must run so request.state.embed_ctx is populated.
    app.add_middleware(
        EmbedAuthMiddleware,
        secret_provider=lambda: settings.EMBED_SIGNING_SECRET,
    )

    app.include_router(task_routes.router, prefix="/api")
    app.dependency_overrides[get_session] = _override_get_session

    return app


# ── Module-level fixtures ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _embed_settings() -> Iterator[None]:
    """Patch embed settings for the duration of each test."""
    original_secret = settings.EMBED_SIGNING_SECRET
    original_origins = settings.EMBED_PARENT_ORIGINS

    settings.EMBED_SIGNING_SECRET = _SIGNING_SECRET
    settings.EMBED_PARENT_ORIGINS = _ALLOWED_ORIGIN

    yield

    settings.EMBED_SIGNING_SECRET = original_secret
    settings.EMBED_PARENT_ORIGINS = original_origins


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield a TestClient backed by a fresh in-memory DB."""
    engine = _make_engine()
    app = _build_app(engine)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    asyncio.new_event_loop().run_until_complete(engine.dispose())


@pytest.fixture
def embed_token() -> str:
    """Mint an embed token scoped to _TASK_ID."""
    token, _ = sign_embed_token(
        secret=_SIGNING_SECRET,
        task_id=_TASK_ID,
        sub=_EMBED_SUB,
        kind="end_user",
        parent_origin=_ALLOWED_ORIGIN,
        ttl_seconds=300,
    )
    return token


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_get_own_task_with_embed_bearer_returns_200(client: TestClient, embed_token: str) -> None:
    """GET /api/tasks/<own_task_id> with valid embed bearer → 200."""
    resp = client.get(
        f"/api/tasks/{_TASK_ID}",
        headers={"Authorization": f"Bearer {embed_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == _TASK_ID


def test_get_other_task_with_embed_bearer_returns_403(client: TestClient, embed_token: str) -> None:
    """GET /api/tasks/<other_task_id> with embed bearer → 403 (out-of-scope)."""
    resp = client.get(
        f"/api/tasks/{_OTHER_TASK_ID}",
        headers={"Authorization": f"Bearer {embed_token}"},
    )
    assert resp.status_code == 403


def test_post_complete_own_task_with_embed_bearer_returns_200(
    client: TestClient, embed_token: str
) -> None:
    """POST /api/tasks/<own_task_id>/complete with embed bearer → 200."""
    resp = client.post(
        f"/api/tasks/{_TASK_ID}/complete",
        json={"response": {"decision": "approved"}},
        headers={"Authorization": f"Bearer {embed_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == _TASK_ID


def test_list_tasks_with_embed_bearer_returns_401_or_403(
    client: TestClient, embed_token: str
) -> None:
    """GET /api/tasks (list) with embed bearer → 401 or 403 (cookie-only)."""
    resp = client.get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {embed_token}"},
    )
    assert resp.status_code in (401, 403), resp.text
