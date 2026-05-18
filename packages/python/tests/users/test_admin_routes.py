"""Admin user CRUD routes — end-to-end via the FastAPI app."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.app import create_app
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    User,
)

ADMIN_TOKEN = "test-admin-token-super-secret"
AUTH = {"X-Admin-Token": ADMIN_TOKEN}


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    import secrets

    from awaithumans.server.core import encryption

    orig_admin = settings.ADMIN_API_TOKEN
    orig_key = settings.PAYLOAD_KEY
    settings.ADMIN_API_TOKEN = ADMIN_TOKEN
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app = create_app(serve_dashboard=False)
    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as c:
        yield c

    await engine.dispose()
    settings.ADMIN_API_TOKEN = orig_admin
    settings.PAYLOAD_KEY = orig_key
    encryption.reset_key_cache()


# ─── Auth gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_admin_token(client: AsyncClient) -> None:
    """No session and no admin token → middleware 401."""
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/users", headers={"X-Admin-Token": "nope"})
    assert resp.status_code == 401


# ─── Create ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_returns_public_fields(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/admin/users",
        headers=AUTH,
        json={
            "email": "alice@example.com",
            "display_name": "Alice",
            "role": "kyc-reviewer",
            "access_level": "senior",
            "password": "hunter2a",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "kyc-reviewer"
    assert body["has_password"] is True
    # Password hash NEVER leaks out of the API.
    assert "password_hash" not in body
    assert "password" not in body


@pytest.mark.asyncio
async def test_create_no_address_returns_422(client: AsyncClient) -> None:
    """Validation happens in the service layer → mapped to 422 via
    UserNoAddressError."""
    resp = await client.post(
        "/api/admin/users",
        headers=AUTH,
        json={"display_name": "Ghost"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "USER_NO_ADDRESS"


@pytest.mark.asyncio
async def test_create_duplicate_email_returns_409(client: AsyncClient) -> None:
    await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    resp = await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "USER_ALREADY_EXISTS"


# ─── List + filter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_users_filter_by_role(client: AsyncClient) -> None:
    await client.post(
        "/api/admin/users",
        headers=AUTH,
        json={"email": "a@example.com", "role": "kyc"},
    )
    await client.post(
        "/api/admin/users",
        headers=AUTH,
        json={"email": "b@example.com", "role": "support"},
    )

    resp = await client.get("/api/admin/users?role=kyc", headers=AUTH)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["email"] == "a@example.com"


# ─── Get / Update / Delete ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_by_id(client: AsyncClient) -> None:
    created = (
        await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    ).json()

    resp = await client.get(f"/api/admin/users/{created['id']}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"


@pytest.mark.asyncio
async def test_get_missing_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/users/no-such-id", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_patch_user_updates_fields(client: AsyncClient) -> None:
    created = (
        await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    ).json()

    resp = await client.patch(
        f"/api/admin/users/{created['id']}",
        headers=AUTH,
        json={"display_name": "Alice", "role": "kyc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Alice"
    assert body["role"] == "kyc"


@pytest.mark.asyncio
async def test_delete_user_removes_row(client: AsyncClient) -> None:
    created = (
        await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    ).json()

    resp = await client.delete(f"/api/admin/users/{created['id']}", headers=AUTH)
    assert resp.status_code == 204

    resp = await client.get(f"/api/admin/users/{created['id']}", headers=AUTH)
    assert resp.status_code == 404


# ─── Password routes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_password_flips_has_password(client: AsyncClient) -> None:
    created = (
        await client.post("/api/admin/users", headers=AUTH, json={"email": "a@example.com"})
    ).json()
    assert created["has_password"] is False

    resp = await client.post(
        f"/api/admin/users/{created['id']}/password",
        headers=AUTH,
        json={"password": "hunter2a"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_password"] is True


@pytest.mark.asyncio
async def test_clear_password_flips_has_password(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/api/admin/users",
            headers=AUTH,
            json={"email": "a@example.com", "password": "hunter2a"},
        )
    ).json()
    assert created["has_password"] is True

    resp = await client.delete(f"/api/admin/users/{created['id']}/password", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["has_password"] is False
