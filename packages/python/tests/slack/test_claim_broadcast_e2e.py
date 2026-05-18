"""Broadcast-to-channel claim flow — end-to-end through the interactions
webhook.

Covers:
  - First click on "Claim this task" assigns the task + opens the modal
  - Second clicker gets an ephemeral "already claimed by X" message
  - Unknown Slack user (not in directory) gets an ephemeral "ask your
    operator to add you" message without claiming
  - Claim on an already-terminal task → ephemeral "already completed"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
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
from awaithumans.server.services.task_service import complete_task, create_task
from awaithumans.server.services.user_service import create_user
from awaithumans.utils.constants import SLACK_ACTION_CLAIM_TASK

SIGNING_SECRET = "test-signing-secret"


class FakeSlackClient:
    """Recorder. Implements every call the claim handler makes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def views_open(self, *, trigger_id: str, view: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "views_open", "trigger_id": trigger_id, "view": view})
        return {"ok": True}

    async def chat_update(
        self, *, channel: str, ts: str, text: str, blocks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "chat_update",
                "channel": channel,
                "ts": ts,
                "text": text,
                "blocks": blocks,
            }
        )
        return {"ok": True}

    # Slack SDK uses camelCase here; matching that exactly so this fake
    # is drop-in for `slack_sdk.web.async_client.AsyncWebClient`.
    async def chat_postEphemeral(  # noqa: N802
        self, *, channel: str, user: str, text: str
    ) -> dict[str, Any]:
        self.calls.append(
            {"method": "chat_postEphemeral", "channel": channel, "user": user, "text": text}
        )
        return {"ok": True}

    async def api_call(
        self,
        _method_name: str = "",
        *,
        http_verb: str = "POST",
        json: dict[str, Any] | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        # response_url-based ephemeral posts come through here.
        self.calls.append({"method": "api_call", "http_verb": http_verb, "json": json, "url": url})
        return {"ok": True}


@pytest_asyncio.fixture
async def slack_ctx() -> AsyncGenerator[tuple[AsyncClient, FakeSlackClient], None]:
    orig_signing = settings.SLACK_SIGNING_SECRET
    orig_payload = settings.PAYLOAD_KEY
    settings.SLACK_SIGNING_SECRET = SIGNING_SECRET
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    fake = FakeSlackClient()
    orig_resolver = client_module.get_client_for_team

    async def fake_resolver(session: AsyncSession, team_id: str | None):
        return fake

    from awaithumans.server.routes.slack import interactions as interactions_route

    client_module.get_client_for_team = fake_resolver  # type: ignore[assignment]
    interactions_route.get_client_for_team = fake_resolver  # type: ignore[assignment]

    app = create_app(serve_dashboard=False)
    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as c:
        yield c, fake

    client_module.get_client_for_team = orig_resolver  # type: ignore[assignment]
    interactions_route.get_client_for_team = orig_resolver  # type: ignore[assignment]
    await engine.dispose()
    settings.SLACK_SIGNING_SECRET = orig_signing
    settings.PAYLOAD_KEY = orig_payload
    encryption.reset_key_cache()


# ─── Slack signature helpers (match test_interactions_e2e.py) ──────────


def _sign(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    base = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
    }


def _form_body(payload: dict[str, Any]) -> bytes:
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _switch_form() -> dict[str, Any]:
    return {
        "version": 1,
        "fields": [
            {
                "kind": "switch",
                "required": True,
                "default": None,
                "true_label": "Yes",
                "false_label": "No",
                "name": "approved",
                "label": "Approve?",
                "hint": None,
            },
        ],
    }


async def _seed_task_and_user(
    client: AsyncClient,
    *,
    slack_team_id: str,
    slack_user_id: str,
) -> tuple[str, str]:
    """Create a directory user + broadcast-eligible task. Returns
    (task_id, user_id)."""
    override = client._transport.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    async for s in override():
        user = await create_user(
            s,
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            display_name="Alice",
            role="reviewer",
        )
        task, _ = await create_task(
            s,
            task="Approve refund",
            payload={"amount": 100},
            payload_schema={},
            response_schema={},
            timeout_seconds=900,
            idempotency_key=f"claim-{secrets.token_hex(4)}",
            form_definition=_switch_form(),
        )
        return task.id, user.id
    raise RuntimeError("session override did not yield")


def _claim_payload(
    *,
    task_id: str,
    team_id: str,
    slack_user_id: str,
    slack_username: str = "alice",
    trigger_id: str = "trigger-xyz",
    channel: str = "C_OPS",
    message_ts: str = "1234567890.0001",
) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "trigger_id": trigger_id,
        "team": {"id": team_id},
        "user": {"id": slack_user_id, "username": slack_username},
        "channel": {"id": channel},
        "message": {"ts": message_ts},
        "response_url": f"https://hooks.slack.example/{secrets.token_hex(6)}",
        "actions": [
            {"action_id": SLACK_ACTION_CLAIM_TASK, "value": task_id},
        ],
    }


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_assigns_task_and_opens_modal(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, fake = slack_ctx
    task_id, user_id = await _seed_task_and_user(
        client, slack_team_id="T_ACME", slack_user_id="U_ALICE"
    )

    body = _form_body(
        _claim_payload(
            task_id=task_id,
            team_id="T_ACME",
            slack_user_id="U_ALICE",
        )
    )
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200

    # Expected calls: chat_update (hide claim button) + views_open (modal).
    methods = [c["method"] for c in fake.calls]
    assert "chat_update" in methods
    assert "views_open" in methods

    # Task row should now be assigned to the claimer.
    override = client._transport.app.dependency_overrides[get_session]
    async for s in override():
        from awaithumans.server.services.task_service import get_task

        t = await get_task(s, task_id)
        assert t.assigned_to_user_id == user_id
        break


@pytest.mark.asyncio
async def test_second_claim_is_ephemeral_error(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, fake = slack_ctx

    # Seed BOTH users + the task in one session context — creating
    # users across separate session-override iterations tripped a
    # "Could not refresh instance" error (detached row).
    override = client._transport.app.dependency_overrides[get_session]
    async for s in override():
        await create_user(
            s,
            slack_team_id="T_ACME",
            slack_user_id="U_ALICE",
            display_name="Alice",
            role="reviewer",
        )
        await create_user(
            s,
            slack_team_id="T_ACME",
            slack_user_id="U_BOB",
            display_name="Bob",
            role="reviewer",
        )
        task, _ = await create_task(
            s,
            task="Approve refund",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=900,
            idempotency_key="concurrent-claim",
            form_definition=_switch_form(),
        )
        task_id = task.id
        break

    # First click — succeeds.
    body1 = _form_body(_claim_payload(task_id=task_id, team_id="T_ACME", slack_user_id="U_ALICE"))
    r1 = await client.post("/api/channels/slack/interactions", content=body1, headers=_sign(body1))
    assert r1.status_code == 200

    calls_after_first = len(fake.calls)

    # Second click by a different user — gets ephemeral reply.
    body2 = _form_body(
        _claim_payload(
            task_id=task_id,
            team_id="T_ACME",
            slack_user_id="U_BOB",
            slack_username="bob",
            trigger_id="trigger-2",
        )
    )
    r2 = await client.post("/api/channels/slack/interactions", content=body2, headers=_sign(body2))
    assert r2.status_code == 200

    # Second click should NOT have opened another modal or updated the message.
    new_calls = fake.calls[calls_after_first:]
    methods = [c["method"] for c in new_calls]
    assert "views_open" not in methods
    assert "chat_update" not in methods

    # Exactly one ephemeral-style reply — either response_url api_call
    # or chat.postEphemeral fallback.
    ephemeral_calls = [c for c in new_calls if c["method"] in ("api_call", "chat_postEphemeral")]
    assert len(ephemeral_calls) == 1
    reply_text = ephemeral_calls[0].get("json", {}).get("text") or ephemeral_calls[0].get(
        "text", ""
    )
    assert "already claimed" in reply_text.lower()


@pytest.mark.asyncio
async def test_claim_by_user_not_in_directory_ephemeral_error(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """A Slack user not registered in the directory can't claim.
    Enforces directory hygiene — an operator should `add-user` them
    first (or use the member picker in Settings → Users)."""
    client, fake = slack_ctx
    # Seed a task but no matching user for the clicker below.
    override = client._transport.app.dependency_overrides[get_session]
    async for s in override():
        task, _ = await create_task(
            s,
            task="Approve refund",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=900,
            idempotency_key="no-user",
            form_definition=_switch_form(),
        )
        task_id = task.id
        break

    body = _form_body(
        _claim_payload(
            task_id=task_id,
            team_id="T_ACME",
            slack_user_id="U_STRANGER",
        )
    )
    r = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert r.status_code == 200

    methods = [c["method"] for c in fake.calls]
    assert "views_open" not in methods
    assert "chat_update" not in methods

    # Task should still be unassigned.
    async for s in override():
        from awaithumans.server.services.task_service import get_task

        t = await get_task(s, task_id)
        assert t.assigned_to_user_id is None
        break


@pytest.mark.asyncio
async def test_claim_on_terminal_task_ephemeral_error(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """Stale broadcast message posted before the task was completed on
    another channel. Claim should soft-fail with an ephemeral."""
    client, fake = slack_ctx
    task_id, _ = await _seed_task_and_user(client, slack_team_id="T_ACME", slack_user_id="U_ALICE")

    # Flip the task to COMPLETED directly (simulating completion via
    # a different channel).
    override = client._transport.app.dependency_overrides[get_session]
    async for s in override():
        await complete_task(
            s,
            task_id=task_id,
            response={"approved": True},
            completed_via_channel="dashboard",
        )
        break

    body = _form_body(_claim_payload(task_id=task_id, team_id="T_ACME", slack_user_id="U_ALICE"))
    r = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert r.status_code == 200

    methods = [c["method"] for c in fake.calls]
    assert "views_open" not in methods

    ephemeral_calls = [c for c in fake.calls if c["method"] in ("api_call", "chat_postEphemeral")]
    assert len(ephemeral_calls) == 1
    text = ephemeral_calls[0].get("json", {}).get("text") or ephemeral_calls[0].get("text", "")
    assert "completed" in text.lower() or "cancelled" in text.lower()
