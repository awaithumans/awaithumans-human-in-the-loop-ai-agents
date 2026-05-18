"""Slack interactivity webhook — end-to-end via the FastAPI app.

Covers `POST /api/channels/slack/interactions` from the signature gate
through to the side effect:

- signature verification (missing headers, bad HMAC, stale timestamp)
- payload shape guards (missing `payload` form field)
- block_actions → loads the task, builds the modal, calls
  `views.open` on the resolved per-team client (via a fake recorder)
- view_submission → coerces Slack values into the task's response
  schema and completes the task (status flips to COMPLETED)
- edge cases: block_actions for a task without form_definition,
  view_submission without private_metadata, unknown payload type

Mirrors the email integration-test style (ASGI AsyncClient + in-memory
SQLite + session override). Slack HTTP calls are replaced with a
`FakeSlackClient` recorder rather than a MagicMock so assertions read
like domain statements ("views.open was called with this view")
instead of mock internals.
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
from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    TaskStatus,
    User,
)
from awaithumans.server.services.task_service import create_task, get_task
from awaithumans.utils.constants import SLACK_ACTION_OPEN_REVIEW

SIGNING_SECRET = "test-signing-secret"


# ─── Fakes + fixtures ───────────────────────────────────────────────────


class FakeSlackClient:
    """Recorder that looks like `slack_sdk.web.async_client.AsyncWebClient`.

    Only implements the methods the route actually calls. Every call is
    recorded; tests inspect `self.calls` instead of untyped MagicMock
    state.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def views_open(self, *, trigger_id: str, view: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "views_open", "trigger_id": trigger_id, "view": view})
        return {"ok": True}


@pytest_asyncio.fixture
async def slack_ctx() -> AsyncGenerator[tuple[AsyncClient, FakeSlackClient], None]:
    """App with SLACK_SIGNING_SECRET + PAYLOAD_KEY configured, in-memory DB,
    and `get_client_for_team` patched to return a single FakeSlackClient
    the test can inspect."""
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

    # Patch the route's client resolver. The route resolves a per-team
    # client via `get_client_for_team(session, team_id)`; we hand back
    # the same fake for every call so the test can assert on it.
    orig_resolver = client_module.get_client_for_team

    async def fake_resolver(session: AsyncSession, team_id: str | None):
        return fake

    # The route imports by attribute reference (`from .client import
    # get_client_for_team`), so we must also patch the route's binding.
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


# ─── Helpers ────────────────────────────────────────────────────────────


def _sign(
    body: bytes,
    *,
    signing_secret: str = SIGNING_SECRET,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Produce the pair of headers Slack would send for a request."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    basestring = b"v0:" + ts.encode() + b":" + body
    sig = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _form_body(payload: dict[str, Any]) -> bytes:
    """Encode a Slack interactivity payload the way Slack actually does."""
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _switch_form() -> dict[str, Any]:
    """Minimal FormDefinition with a single switch field — enough to
    drive a view_submission through `slack_values_to_response`."""
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


async def _seed_task(client: AsyncClient, *, form_definition: dict[str, Any] | None) -> str:
    """Create a task directly in the overridden DB and return its id."""
    override = client._transport.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    async for s in override():
        task, _ = await create_task(
            s,
            task="Approve refund",
            payload={"amount": 100},
            payload_schema={},
            response_schema={},
            timeout_seconds=900,
            idempotency_key=f"slack-e2e-{secrets.token_hex(4)}",
            form_definition=form_definition,
        )
        return task.id
    raise RuntimeError("session override did not yield")


async def _read_task(client: AsyncClient, task_id: str) -> Task:
    override = client._transport.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    async for s in override():
        return await get_task(s, task_id)
    raise RuntimeError("session override did not yield")


async def _seed_user(
    client: AsyncClient,
    *,
    email: str,
    slack_team_id: str,
    slack_user_id: str,
    is_operator: bool = False,
) -> str:
    """Insert a directory user with a Slack identity. Required so the
    interactivity routes can resolve a Slack user to a directory user
    and authorise them against the task."""
    override = client._transport.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    async for s in override():
        user = User(
            email=email,
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            is_operator=is_operator,
            active=True,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id
    raise RuntimeError("session override did not yield")


# ─── Signature gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interactions_rejects_missing_signature(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    body = _form_body({"type": "block_actions", "actions": []})
    resp = await client.post(
        "/api/channels/slack/interactions",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_interactions_rejects_bad_signature(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    body = _form_body({"type": "block_actions", "actions": []})
    headers = _sign(body)
    headers["X-Slack-Signature"] = "v0=" + "0" * 64  # well-formed but wrong
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_interactions_rejects_stale_timestamp(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    body = _form_body({"type": "block_actions", "actions": []})
    # 10 minutes old — the signing module caps at 5 minutes.
    headers = _sign(body, timestamp=int(time.time()) - 600)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_interactions_503_when_secret_unset(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    orig = settings.SLACK_SIGNING_SECRET
    settings.SLACK_SIGNING_SECRET = None
    try:
        resp = await client.post(
            "/api/channels/slack/interactions",
            content=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 503
    finally:
        settings.SLACK_SIGNING_SECRET = orig


@pytest.mark.asyncio
async def test_interactions_400_on_missing_payload(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    # Signed but empty body — signature verifies, but the form has no `payload` key.
    resp = await client.post("/api/channels/slack/interactions", content=b"", headers=_sign(b""))
    assert resp.status_code == 400


# ─── block_actions → views.open ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_block_actions_opens_modal(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, fake = slack_ctx
    # Seed an operator — their Slack identity must be resolvable to a
    # directory user before the route will open the modal. Operators
    # can act on any task; non-operators only on their own.
    await _seed_user(
        client,
        email="ops@acme.com",
        slack_team_id="T_ACME",
        slack_user_id="U_ALICE",
        is_operator=True,
    )
    task_id = await _seed_task(client, form_definition=_switch_form())

    payload = {
        "type": "block_actions",
        "trigger_id": "trigger-xyz",
        "team": {"id": "T_ACME"},
        "user": {"id": "U_ALICE"},
        "actions": [
            {"action_id": SLACK_ACTION_OPEN_REVIEW, "value": task_id},
        ],
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200

    # views.open should have been called exactly once with a modal view
    # whose private_metadata carries the task_id.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "views_open"
    assert call["trigger_id"] == "trigger-xyz"
    view = call["view"]
    assert view["type"] == "modal"
    assert view["private_metadata"] == task_id
    # Block Kit modal should contain at least one input block for the switch.
    assert any(b.get("type") == "input" for b in view["blocks"])


@pytest.mark.asyncio
async def test_block_actions_ignores_non_open_action(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """A click on the dashboard link-out button (different action_id) is a no-op."""
    client, fake = slack_ctx
    payload = {
        "type": "block_actions",
        "trigger_id": "t",
        "team": {"id": "T"},
        "actions": [{"action_id": "awaithumans.open_dashboard", "value": "ignored"}],
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200
    assert fake.calls == []


@pytest.mark.asyncio
async def test_block_actions_silent_when_task_has_no_form(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """Task without form_definition → log-and-skip, no modal opened."""
    client, fake = slack_ctx
    task_id = await _seed_task(client, form_definition=None)
    payload = {
        "type": "block_actions",
        "trigger_id": "t",
        "team": {"id": "T"},
        "actions": [{"action_id": SLACK_ACTION_OPEN_REVIEW, "value": task_id}],
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200
    assert fake.calls == []


# ─── view_submission → complete task ────────────────────────────────────


@pytest.mark.asyncio
async def test_view_submission_completes_task(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    client, _ = slack_ctx
    # Seed an operator with a Slack identity — they're authorised to
    # complete any task, and the directory email becomes the audited
    # `completed_by_email` (NOT the trusted-from-Slack username).
    await _seed_user(
        client,
        email="alice@acme.com",
        slack_team_id="T_ACME",
        slack_user_id="U_ALICE",
        is_operator=True,
    )
    task_id = await _seed_task(client, form_definition=_switch_form())

    # Slack sends back the user's selections under `view.state.values`,
    # keyed by block_id. Our coerce layer maps that into the form's
    # field names — here, `approved` → True (from selecting the "true"
    # option on a radio_buttons element).
    payload = {
        "type": "view_submission",
        "team": {"id": "T_ACME"},
        "user": {"id": "U_ALICE", "username": "alice"},
        "view": {
            "private_metadata": task_id,
            "state": {
                "values": {
                    "awaithumans:approved": {
                        "approved": {
                            "type": "radio_buttons",
                            "selected_option": {"value": "true"},
                        },
                    },
                },
            },
        },
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200
    assert resp.json() == {}  # empty response closes the modal

    updated = await _read_task(client, task_id)
    assert updated.status == TaskStatus.COMPLETED
    assert updated.response == {"approved": True}
    assert updated.completed_via_channel == "slack"
    assert updated.completed_by_email == "alice@acme.com"


# ─── Auth gates: only directory operators / assignees can act ────────────


@pytest.mark.asyncio
async def test_block_actions_blocked_for_non_assignee_non_operator(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """A directory user who is NEITHER the task's assignee NOR an
    operator must not be able to open the review modal — the broadcast
    "Claim" path is the only legitimate way for a non-assignee to
    take a task. Without this gate, anyone in a shared channel who
    saw the message could submit on behalf of the actual assignee."""
    client, fake = slack_ctx
    # Bob exists in the directory but is not an operator and is not
    # the task's assignee.
    await _seed_user(
        client,
        email="bob@acme.com",
        slack_team_id="T_ACME",
        slack_user_id="U_BOB",
        is_operator=False,
    )
    task_id = await _seed_task(client, form_definition=_switch_form())

    payload = {
        "type": "block_actions",
        "trigger_id": "trigger-xyz",
        "team": {"id": "T_ACME"},
        "user": {"id": "U_BOB"},
        "actions": [
            {"action_id": SLACK_ACTION_OPEN_REVIEW, "value": task_id},
        ],
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200
    # Modal NOT opened — the route returns 200 but does no Slack work
    # beyond the ephemeral "you're not authorised" reply path.
    assert all(c["method"] != "views_open" for c in fake.calls)


@pytest.mark.asyncio
async def test_view_submission_blocked_for_non_assignee_non_operator(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """Even with a captured `private_metadata` task_id, a non-assignee
    non-operator must not be able to complete a task by submitting the
    modal directly. Defense in depth — `_open_modal_for_task` already
    blocks before opening, but the submit handler is the hard gate."""
    client, _ = slack_ctx
    await _seed_user(
        client,
        email="bob@acme.com",
        slack_team_id="T_ACME",
        slack_user_id="U_BOB",
        is_operator=False,
    )
    task_id = await _seed_task(client, form_definition=_switch_form())

    payload = {
        "type": "view_submission",
        "team": {"id": "T_ACME"},
        "user": {"id": "U_BOB"},
        "view": {
            "private_metadata": task_id,
            "state": {
                "values": {
                    "awaithumans:approved": {
                        "approved": {
                            "type": "radio_buttons",
                            "selected_option": {"value": "true"},
                        },
                    },
                },
            },
        },
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    # Modal returns an error block instead of empty — task NOT completed.
    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json.get("response_action") == "errors"

    updated = await _read_task(client, task_id)
    assert updated.status != TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_view_submission_missing_metadata_400(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """A modal submission without private_metadata can't be routed to a task."""
    client, _ = slack_ctx
    payload = {
        "type": "view_submission",
        "user": {"username": "a"},
        "view": {"state": {"values": {}}},
    }
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 400


# ─── Unknown payload type ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_payload_type_is_no_op(
    slack_ctx: tuple[AsyncClient, FakeSlackClient],
) -> None:
    """Slack emits many interaction types (shortcut, message_action, …) we
    don't handle yet. They should 200 with no body and not throw."""
    client, fake = slack_ctx
    payload = {"type": "shortcut", "user": {"id": "U"}}
    body = _form_body(payload)
    resp = await client.post("/api/channels/slack/interactions", content=body, headers=_sign(body))
    assert resp.status_code == 200
    assert fake.calls == []
