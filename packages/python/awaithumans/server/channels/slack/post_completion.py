"""Update Slack messages when a task transitions to a terminal state.

The notifier posts a message with action buttons (Open in Dashboard /
Claim This Task). Without a follow-up update, that message stays
interactive forever — operators reading their DMs days later see an
"open" task that's been done. This module is the follow-up: load the
SlackTaskMessage rows for a task, call `chat.update` on each with a
new "Completed by X" / "Cancelled" / "Timed out" surface that has no
buttons.

Designed to be fired as a FastAPI BackgroundTask (or asyncio task
from the timeout scheduler) so a slow Slack API call never blocks
the human's submit / cancel / the scheduler tick. Slack errors are
logged, not raised — the cosmetic message lagging behind shouldn't
fail the task lifecycle.

The helper takes a snapshot of (task_id, task_title, status,
completed_by_display) at call time so it doesn't depend on the
caller passing the task object — the route handler may have closed
its session by the time the background task runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from awaithumans.server.channels.slack.blocks import terminal_message_blocks
from awaithumans.server.channels.slack.client import (
    get_client_for_team,
    get_default_client,
)
from awaithumans.server.channels.slack.handoff_url import (
    build_review_url,
    task_handoff_expiry,
)
from awaithumans.server.channels.slack.handoff_url_types import HandoffParams
from awaithumans.server.channels.slack.message_log import (
    list_messages_for_task,
)
from awaithumans.server.db.connection import get_async_session_factory
from awaithumans.server.services.task_service import get_task

if TYPE_CHECKING:  # pragma: no cover
    from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger("awaithumans.server.channels.slack.post_completion")


async def update_slack_messages_for_task(task_id: str) -> None:
    """Replace each persisted Slack message with a terminal-state surface.

    Best-effort throughout: SQL errors, missing tasks, dropped Slack
    permissions, removed messages — all logged and swallowed. The only
    hard failure is import-time (module didn't load). A task whose
    Slack messages can't be updated is a UI papercut, not a data
    integrity issue.
    """
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            task = await get_task(session, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("post_completion: failed to load task=%s: %s", task_id, exc)
            return

        messages = await list_messages_for_task(session, task_id)
        if not messages:
            # No Slack messages were posted for this task (notify=
            # was email-only, or Slack was misconfigured at create
            # time). Nothing to update.
            return

        # The "View in dashboard" button on the terminal surface must
        # also be a signed handoff URL when we know who the assignee
        # is — Slack-only users still need to be able to click through
        # and see the response that was submitted. When the task has
        # no resolved assignee (broadcast that nobody claimed before
        # cancel/timeout), we drop back to the unsigned URL.
        handoff = _handoff_for_task(task)
        review_url = build_review_url(task_id=task.id, params=handoff)

        completed_by_display = task.completed_by_email
        status = task.status.value if hasattr(task.status, "value") else str(task.status)

        for msg in messages:
            client = await _resolve_client(team_id=msg.team_id, session=session)
            if client is None:
                logger.info(
                    "post_completion: no Slack client for team=%s; skipping message ts=%s",
                    msg.team_id,
                    msg.ts,
                )
                continue

            blocks = terminal_message_blocks(
                task_title=task.task,
                status=status,
                completed_by_display=completed_by_display,
                review_url=review_url,
            )
            try:
                await client.chat_update(
                    channel=msg.channel,
                    ts=msg.ts,
                    text=f"{status}: {task.task}",
                    blocks=blocks,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "post_completion: chat.update failed for task=%s channel=%s ts=%s: %s",
                    task_id,
                    msg.channel,
                    msg.ts,
                    exc,
                )


def _handoff_for_task(task) -> HandoffParams | None:  # noqa: ANN001 — Task model
    if not task.assigned_to_user_id or not task.timeout_at:
        return None
    return HandoffParams(
        user_id=task.assigned_to_user_id,
        exp_unix=task_handoff_expiry(task.timeout_at),
    )


async def _resolve_client(*, team_id: str | None, session) -> AsyncWebClient | None:
    """Pick the Slack client matching the message's workspace.

    We use the SAME workspace we posted from. If the OAuth installation
    was revoked between post and update, we get None and log; we don't
    fall back to the default workspace because that would post into the
    wrong tenant in multi-workspace setups."""
    if team_id:
        return await get_client_for_team(session, team_id)
    return await get_default_client(session)
