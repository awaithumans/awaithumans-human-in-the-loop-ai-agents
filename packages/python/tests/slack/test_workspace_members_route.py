"""GET /api/channels/slack/installations/{team_id}/members — Slack
workspace member picker backend.

Patches `get_client_for_team` to return a fake that yields a known
users.list payload, then asserts the route transforms/filters correctly.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.app import create_app
from awaithumans.server.channels.slack import client as client_module
from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import (  # noqa: F401 — register
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    User,
)
from awaithumans.server.services.user_service import create_user


class _UsersListResp:
    def __init__(self, members: list[dict[str, Any]]) -> None:
        self.data = {"ok": True, "members": members}


class FakeClient:
    """Just enough to satisfy the members route."""

    def __init__(self, members: list[dict[str, Any]]) -> None:
        self._members = members

    async def users_list(self) -> _UsersListResp:
        return _UsersListResp(self._members)


class FailingClient:
    """Simulates Slack rejecting users.list (e.g. missing scope).

    Raises `SlackApiError` because that's what the real
    AsyncWebClient raises — the route's specific catch is keyed on
    that exception type, so a generic RuntimeError here would
    propagate past the handler and 500 instead of 502."""

    async def users_list(self) -> Any:
        from slack_sdk.errors import SlackApiError

        raise SlackApiError(
            message="missing_scope: users:read",
            response={"ok": False, "error": "missing_scope"},
        )


# Canonical sample of users.list shape — real Slack response fields,
# trimmed to what the route actually reads.
SAMPLE_MEMBERS: list[dict[str, Any]] = [
    {
        "id": "U_ALICE",
        "name": "alice",
        "real_name": "Alice Singh",
        "is_bot": False,
        "is_admin": True,
        "deleted": False,
        "profile": {"real_name": "Alice Singh", "display_name": "alice.s"},
    },
    {
        "id": "U_BOB",
        "name": "bob",
        "real_name": "Bob",
        "is_bot": False,
        "is_admin": False,
        "deleted": False,
        "profile": {"real_name": "Bob", "display_name": ""},
    },
    {
        "id": "USLACKBOT",
        "name": "slackbot",
        "real_name": "Slackbot",
        "is_bot": False,
        "is_admin": False,
        "deleted": False,
        "profile": {"real_name": "Slackbot"},
    },
    {
        "id": "B_HOOK",
        "name": "hookbot",
        "real_name": "Hook Bot",
        "is_bot": True,
        "is_admin": False,
        "deleted": False,
        "profile": {"real_name": "Hook Bot"},
    },
    {
        "id": "U_GONE",
        "name": "gone",
        "real_name": "Former Employee",
        "is_bot": False,
        "is_admin": False,
        "deleted": True,
        "profile": {"real_name": "Former Employee"},
    },
]


@pytest_asyncio.fixture
async def logged_in_client() -> AsyncGenerator[tuple[AsyncClient, AsyncSession], None]:
    """Operator-session-authed client. The members route is admin-only."""
    orig_payload = settings.PAYLOAD_KEY
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
        # Seed an operator and log them in so subsequent requests ride
        # the session cookie.
        async with factory() as s:
            await create_user(
                s,
                email="op@example.com",
                is_operator=True,
                password="hunter2a",
            )

        resp = await c.post(
            "/api/auth/login",
            json={"email": "op@example.com", "password": "hunter2a"},
        )
        assert resp.status_code == 204

        yield c, factory

    await engine.dispose()
    settings.PAYLOAD_KEY = orig_payload
    encryption.reset_key_cache()


def _patch_resolver(client_fn):
    """Patch the resolver in both the client module AND the
    installations route (which imported by name)."""
    from awaithumans.server.routes.slack import installations as inst

    orig_module = client_module.get_client_for_team
    orig_route = inst.get_client_for_team
    client_module.get_client_for_team = client_fn  # type: ignore[assignment]
    inst.get_client_for_team = client_fn  # type: ignore[assignment]

    def restore() -> None:
        client_module.get_client_for_team = orig_module  # type: ignore[assignment]
        inst.get_client_for_team = orig_route  # type: ignore[assignment]

    return restore


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_members_filters_bots_deactivated_and_slackbot(
    logged_in_client: tuple[AsyncClient, Any],
) -> None:
    c, _ = logged_in_client

    async def fake_resolver(session, team_id):
        return FakeClient(SAMPLE_MEMBERS)

    restore = _patch_resolver(fake_resolver)
    try:
        resp = await c.get("/api/channels/slack/installations/T_ACME/members")
        assert resp.status_code == 200
        rows = resp.json()
    finally:
        restore()

    ids = [r["id"] for r in rows]
    assert "U_ALICE" in ids
    assert "U_BOB" in ids
    assert "USLACKBOT" not in ids
    assert "B_HOOK" not in ids
    assert "U_GONE" not in ids

    # Alphabetical order — Alice before Bob.
    assert ids == ["U_ALICE", "U_BOB"]

    alice = next(r for r in rows if r["id"] == "U_ALICE")
    assert alice["name"] == "alice"
    assert alice["real_name"] == "Alice Singh"
    assert alice["display_name"] == "alice.s"
    assert alice["is_admin"] is True


@pytest.mark.asyncio
async def test_members_404_when_no_installation(
    logged_in_client: tuple[AsyncClient, Any],
) -> None:
    c, _ = logged_in_client

    async def fake_resolver(session, team_id):
        return None

    restore = _patch_resolver(fake_resolver)
    try:
        resp = await c.get("/api/channels/slack/installations/T_MISSING/members")
    finally:
        restore()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_members_502_when_slack_rejects(
    logged_in_client: tuple[AsyncClient, Any],
) -> None:
    c, _ = logged_in_client

    async def fake_resolver(session, team_id):
        return FailingClient()

    restore = _patch_resolver(fake_resolver)
    try:
        resp = await c.get("/api/channels/slack/installations/T_ACME/members")
    finally:
        restore()

    assert resp.status_code == 502
    body = resp.json()
    assert "users:read" in body.get("detail", "") or "Slack" in body.get("detail", "")
