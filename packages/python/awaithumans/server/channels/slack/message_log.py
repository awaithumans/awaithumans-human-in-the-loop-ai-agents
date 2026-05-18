"""Persist references to Slack messages we posted for tasks.

Used by the notifier (one row per chat.postMessage) and by the post-
completion updater (load all rows for a task → chat.update each).
Lives in its own module so the model + tiny DAL are decoupled from
the heavy notifier.

We do not enforce a unique constraint on (task_id, channel, ts) — a
duplicate row is harmless (the post-completion updater runs
chat.update twice with the same payload, second is a no-op) and the
extra index would slow inserts on the hot path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from awaithumans.server.db.models import SlackTaskMessage

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("awaithumans.server.channels.slack.message_log")


async def record_posted_message(
    session: AsyncSession,
    *,
    task_id: str,
    channel: str,
    ts: str,
    team_id: str | None,
) -> None:
    """Insert one SlackTaskMessage row. Caller commits.

    Best-effort: if the insert fails (e.g., the task row was deleted
    between post and insert) we log and move on — we'd rather not
    fail the notification path on a bookkeeping miss."""
    if not channel or not ts:
        # Slack returned an unexpected response shape; skip the row
        # rather than insert a useless reference.
        logger.warning(
            "record_posted_message: missing channel/ts for task=%s; skipping",
            task_id,
        )
        return

    row = SlackTaskMessage(task_id=task_id, channel=channel, ts=ts, team_id=team_id)
    session.add(row)


async def list_messages_for_task(session: AsyncSession, task_id: str) -> list[SlackTaskMessage]:
    """All Slack messages we've posted for the given task.

    The order matches insertion order in practice (PK is monotonic-
    ish UUID hex; the ORDER BY exists so callers don't depend on
    implementation accidents)."""
    result = await session.execute(
        select(SlackTaskMessage)
        .where(SlackTaskMessage.task_id == task_id)
        .order_by(SlackTaskMessage.created_at.asc())
    )
    return list(result.scalars().all())
