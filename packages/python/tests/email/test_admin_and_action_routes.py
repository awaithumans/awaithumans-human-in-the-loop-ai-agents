"""Admin CRUD + magic-link action routes — end-to-end via the FastAPI app."""

from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.app import create_app
from awaithumans.server.channels.email.magic_links import sign_action_token
from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    TaskStatus,
)
from awaithumans.server.services.task_service import create_task

ADMIN_TOKEN = "test-admin-token-super-secret"


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """App with DB override + encryption key + admin token set."""
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


# ─── Admin auth gating ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_missing_token(client: AsyncClient) -> None:
    """No session, no admin token → middleware 401. Post-A3 the same
    401 covers both "not logged in" and "wrong admin token" — admin is
    reachable either by session (operator) or bearer, but not both
    missing."""
    resp = await client.get("/api/channels/email/identities")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_wrong_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/channels/email/identities",
        headers={"X-Admin-Token": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoint_401_when_token_unset(client: AsyncClient) -> None:
    """When ADMIN_API_TOKEN is unset, the admin bearer path is off —
    but operator-session auth remains. An anonymous caller still gets
    401 (middleware), not 503 (the old "feature-disabled" semantics
    disappear since operators can always reach admin via login)."""
    orig = settings.ADMIN_API_TOKEN
    settings.ADMIN_API_TOKEN = None
    try:
        resp = await client.get(
            "/api/channels/email/identities",
            headers={"X-Admin-Token": "anything"},
        )
        assert resp.status_code == 401
    finally:
        settings.ADMIN_API_TOKEN = orig


# ─── Identity CRUD ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_identity_full_lifecycle(client: AsyncClient) -> None:
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    create = await client.post(
        "/api/channels/email/identities",
        headers=headers,
        json={
            "id": "acme-prod",
            "display_name": "Acme Prod",
            "from_email": "notifications@acme.com",
            "from_name": "Acme Tasks",
            "transport": "noop",
            "transport_config": {},
        },
    )
    assert create.status_code == 200, create.text
    data = create.json()
    assert data["id"] == "acme-prod"
    # transport_config never echoed back — prevents exfiltration.
    assert "transport_config" not in data

    listing = await client.get("/api/channels/email/identities", headers=headers)
    assert listing.status_code == 200
    assert any(i["id"] == "acme-prod" for i in listing.json())

    fetched = await client.get("/api/channels/email/identities/acme-prod", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["from_email"] == "notifications@acme.com"

    deleted = await client.delete("/api/channels/email/identities/acme-prod", headers=headers)
    assert deleted.status_code == 204

    gone = await client.get("/api/channels/email/identities/acme-prod", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_create_identity_validates_transport_config(
    client: AsyncClient,
) -> None:
    """Bad transport config is caught on create, not at send time."""
    resp = await client.post(
        "/api/channels/email/identities",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={
            "id": "bad",
            "display_name": "Bad",
            "from_email": "x@x.com",
            "transport": "resend",
            "transport_config": {},  # missing api_key
        },
    )
    assert resp.status_code == 400
    assert "api_key" in resp.text


# ─── Magic-link action: GET confirm page + POST submit ──────────────────


@pytest.mark.asyncio
async def test_action_invalid_token_renders_error_page(client: AsyncClient) -> None:
    resp = await client.get("/api/channels/email/action/bogus-token")
    assert resp.status_code == 400
    assert "expired" in resp.text.lower() or "invalid" in resp.text.lower()


@pytest.mark.asyncio
async def test_action_get_renders_confirmation_page(client: AsyncClient) -> None:
    """Valid token on a live task → 200 with a POST form (anti-prefetch)."""
    # Create a task directly in the DB so we have a real task_id to sign.
    async for session in _direct_session(client):
        task, _ = await create_task(
            session,
            task="Approve wire",
            payload={"amount": 50000},
            payload_schema={},
            response_schema={},
            timeout_seconds=3600,
            idempotency_key="k1",
        )
        task_id = task.id
        break

    token = sign_action_token(task_id=task_id, field_name="approve", value=True)
    resp = await client.get(f"/api/channels/email/action/{token}")
    assert resp.status_code == 200
    # The GET renders a <form method="POST"> — no state mutated yet.
    assert '<form method="POST"' in resp.text
    assert "Approve wire" in resp.text


@pytest.mark.asyncio
async def test_action_post_completes_task(client: AsyncClient) -> None:
    async for session in _direct_session(client):
        task, _ = await create_task(
            session,
            task="Approve wire",
            payload={"amount": 50000},
            payload_schema={},
            response_schema={},
            timeout_seconds=3600,
            idempotency_key="k2",
        )
        task_id = task.id
        break

    token = sign_action_token(
        task_id=task_id,
        field_name="approve",
        value=True,
        recipient="reviewer@acme.com",
    )
    resp = await client.post(f"/api/channels/email/action/{token}")
    assert resp.status_code == 200
    assert "recorded" in resp.text.lower() or "thanks" in resp.text.lower()

    # Verify the task is actually completed with the signed value AND
    # that the recipient email landed on `completed_by_email` so the
    # audit log isn't a black hole for email completions.
    async for session in _direct_session(client):
        from awaithumans.server.services.task_service import get_task

        updated = await get_task(session, task_id)
        assert updated.status == TaskStatus.COMPLETED
        assert updated.response == {"approve": True}
        assert updated.completed_via_channel == "email"
        assert updated.completed_by_email == "reviewer@acme.com"
        # No directory user with that email → user_id stays null;
        # email-only attribution still wins over the previous
        # null-everything behavior.
        assert updated.completed_by_user_id is None
        break


@pytest.mark.asyncio
async def test_action_post_pre_feature_token_leaves_completed_by_null(
    client: AsyncClient,
) -> None:
    """A token signed without the new `recipient` field (i.e., before
    this fix shipped) must still verify and complete the task — just
    without the email attribution. Pre-feature in-flight tokens at
    deploy time would otherwise 500."""
    async for session in _direct_session(client):
        task, _ = await create_task(
            session,
            task="Approve wire",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=3600,
            idempotency_key="k-no-recipient",
        )
        task_id = task.id
        break

    # `recipient=None` (default) → token's signed body omits `r`.
    token = sign_action_token(task_id=task_id, field_name="approve", value=True)
    resp = await client.post(f"/api/channels/email/action/{token}")
    assert resp.status_code == 200

    async for session in _direct_session(client):
        from awaithumans.server.services.task_service import get_task

        updated = await get_task(session, task_id)
        assert updated.status == TaskStatus.COMPLETED
        assert updated.completed_by_email is None
        assert updated.completed_by_user_id is None
        break


@pytest.mark.asyncio
async def test_action_post_replay_rejected_with_410(
    client: AsyncClient,
) -> None:
    """Single-use enforcement: the second POST of the same magic-link
    token returns 410 with an "already used" message, BEFORE touching
    `complete_task`. Without this, a forwarded email or leaked URL is
    replayable for the entire TTL window.

    410 (rather than 200 with a status message) tells caches /
    proxies / mail clients the resource is permanently gone — they
    should not retry."""
    async for session in _direct_session(client):
        task, _ = await create_task(
            session,
            task="Approve",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=3600,
            idempotency_key="k3",
        )
        task_id = task.id
        break

    token = sign_action_token(task_id=task_id, field_name="approve", value=True)
    first = await client.post(f"/api/channels/email/action/{token}")
    assert first.status_code == 200

    # Second click of the same link is rejected as already used.
    second = await client.post(f"/api/channels/email/action/{token}")
    assert second.status_code == 410
    assert "already" in second.text.lower()


@pytest.mark.asyncio
async def test_action_post_two_distinct_tokens_for_same_task_independent(
    client: AsyncClient,
) -> None:
    """The single-use marker is keyed on the token's `jti`, not on
    `(task_id, field_name)`. An operator who issued two different
    magic-link tokens for the same option (e.g. resent the email)
    should be able to consume EITHER one — once. This test pins that
    the consumed-token table doesn't accidentally over-block."""
    async for session in _direct_session(client):
        task, _ = await create_task(
            session,
            task="Approve",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=3600,
            idempotency_key="k4",
        )
        task_id = task.id
        break

    token_a = sign_action_token(task_id=task_id, field_name="approve", value=True)
    token_b = sign_action_token(task_id=task_id, field_name="approve", value=True)
    assert token_a != token_b  # different jti each time

    # First token consumed successfully.
    resp_a = await client.post(f"/api/channels/email/action/{token_a}")
    assert resp_a.status_code == 200

    # Second token tries to complete the (now terminal) task and gets
    # the "already completed" path — proves single-use didn't block
    # the unrelated jti, AND proves the terminal-state check still
    # works as a backstop for legitimate-but-stale links.
    resp_b = await client.post(f"/api/channels/email/action/{token_b}")
    assert resp_b.status_code == 200
    assert "already" in resp_b.text.lower()


async def _direct_session(client: AsyncClient) -> AsyncGenerator[AsyncSession, None]:
    """Reach into the app's dependency override to get a session for direct DB use."""
    override = client._transport.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    async for s in override():
        yield s
