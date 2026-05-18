"""End-to-end test for the post-completion Slack message updater.

When a task transitions to a terminal state (complete / cancel /
timeout), `update_slack_messages_for_task` walks the SlackTaskMessage
rows for that task and calls `chat.update` on each. These tests pin
the contract:

  - Every recorded message gets an update call
  - The block payload comes from `terminal_message_blocks` (no buttons)
  - chat.update errors are logged, not raised — a Slack outage must
    not bubble out and break the calling completion path

The test patches the client resolver and the session factory so the
flow runs end-to-end against an in-memory SQLite without any real
Slack network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.channels.slack.message_log import record_posted_message
from awaithumans.server.channels.slack.post_completion import (
    update_slack_messages_for_task,
)
from awaithumans.server.db.models import (  # noqa: F401 — registers metadata
    AuditEntry,
    SlackInstallation,
    SlackTaskMessage,
    Task,
)
from awaithumans.types import TaskStatus

# ─── Fakes ───────────────────────────────────────────────────────────


class _FakeClient:
    """Records every chat.update call as (channel, ts, text, blocks)."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail_with

    async def chat_update(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        if self._fail is not None:
            raise self._fail
        return {"ok": True}


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_and_factory():
    """In-memory DB + a factory the production code will read via
    `get_async_session_factory`. We patch that lookup to return
    THIS factory so post_completion uses our fixture's DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_task_with_messages(
    factory,
    *,
    task_id: str = "task-001",
    status: TaskStatus = TaskStatus.COMPLETED,
    messages: list[dict[str, str]] | None = None,
) -> None:
    """Insert one Task row + N SlackTaskMessage rows so the updater
    has something to walk."""
    now = datetime.now(timezone.utc)
    async with factory() as session:
        task = Task(
            id=task_id,
            task="Approve KYC",
            payload={},
            payload_schema={},
            response_schema={},
            timeout_seconds=600,
            idempotency_key=f"idem-{task_id}",
            status=status,
            created_at=now,
            updated_at=now,
            timeout_at=now + timedelta(minutes=10),
            completed_at=now,
            completed_by_email="op@example.com",
        )
        session.add(task)
        for msg in messages or []:
            await record_posted_message(
                session,
                task_id=task_id,
                channel=msg["channel"],
                ts=msg["ts"],
                team_id=msg.get("team_id"),
            )
        await session.commit()


# ─── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_updates_every_recorded_message(session_and_factory) -> None:
    """Two messages → two chat.update calls."""
    await _seed_task_with_messages(
        session_and_factory,
        messages=[
            {"channel": "D_ALICE", "ts": "1700000000.000001"},
            {"channel": "C_GENERAL", "ts": "1700000000.000002"},
        ],
    )

    fake = _FakeClient()
    with (
        patch(
            "awaithumans.server.channels.slack.post_completion.get_async_session_factory",
            return_value=session_and_factory,
        ),
        patch(
            "awaithumans.server.channels.slack.post_completion.get_default_client",
            return_value=fake,
        ),
    ):
        await update_slack_messages_for_task("task-001")

    assert len(fake.calls) == 2
    channels = {c["channel"] for c in fake.calls}
    assert channels == {"D_ALICE", "C_GENERAL"}


@pytest.mark.asyncio
async def test_no_messages_means_no_calls(session_and_factory) -> None:
    """A task with no SlackTaskMessage rows (email-only notify)
    must not cause chat.update calls."""
    await _seed_task_with_messages(session_and_factory, messages=[])

    fake = _FakeClient()
    with (
        patch(
            "awaithumans.server.channels.slack.post_completion.get_async_session_factory",
            return_value=session_and_factory,
        ),
        patch(
            "awaithumans.server.channels.slack.post_completion.get_default_client",
            return_value=fake,
        ),
    ):
        await update_slack_messages_for_task("task-001")

    assert fake.calls == []


@pytest.mark.asyncio
async def test_chat_update_failures_are_swallowed(session_and_factory) -> None:
    """Slack returning an error (lost permissions, deleted message,
    etc.) must not bubble out — the caller's task lifecycle is more
    important than the cosmetic update."""
    await _seed_task_with_messages(
        session_and_factory,
        messages=[{"channel": "D_ALICE", "ts": "1700000000.000001"}],
    )

    fake = _FakeClient(fail_with=RuntimeError("slack outage"))
    with (
        patch(
            "awaithumans.server.channels.slack.post_completion.get_async_session_factory",
            return_value=session_and_factory,
        ),
        patch(
            "awaithumans.server.channels.slack.post_completion.get_default_client",
            return_value=fake,
        ),
    ):
        # No raise == swallowed.
        await update_slack_messages_for_task("task-001")

    # We still attempted the call.
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_posted_blocks_have_no_action_buttons(session_and_factory) -> None:
    """After completion the recipient must not be able to re-open
    the modal — no actions block / no action_id without a url."""
    await _seed_task_with_messages(
        session_and_factory,
        messages=[{"channel": "D_ALICE", "ts": "1700000000.000001"}],
    )

    fake = _FakeClient()
    with (
        patch(
            "awaithumans.server.channels.slack.post_completion.get_async_session_factory",
            return_value=session_and_factory,
        ),
        patch(
            "awaithumans.server.channels.slack.post_completion.get_default_client",
            return_value=fake,
        ),
    ):
        await update_slack_messages_for_task("task-001")

    [call] = fake.calls
    for block in call["blocks"]:
        for element in block.get("elements", []):
            if "action_id" in element and "url" not in element:
                raise AssertionError(f"Terminal block has interactive element: {element}")
