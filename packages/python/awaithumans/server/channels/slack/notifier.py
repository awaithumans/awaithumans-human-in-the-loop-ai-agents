"""Slack task notification — the public entry point from task creation.

Parses the task's `notify` list for Slack routes, resolves the workspace
(static env token, stored OAuth installation, or identity-suffixed
`slack+T123456:#channel` to disambiguate multi-workspace setups), and
posts the initial message.

DM target resolution (`slack:@alice`) lives in `resolution.py` so it
can be reused by the task router for implicit-assignee derivation.

Runs in a FastAPI BackgroundTask after the response is sent, so a slow
Slack API call never blocks task creation and a Slack outage doesn't
fail a successful task write. The notifier acquires its own DB session
because the caller's session has already been released by the time we run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from awaithumans.forms import FormDefinition, unsupported_fields
from awaithumans.server.channels.routing import ChannelRoute, routes_for_channel
from awaithumans.server.channels.slack.blocks import open_review_message_blocks
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
    record_posted_message,
)
from awaithumans.server.channels.slack.resolution import resolve_slack_target
from awaithumans.server.db.connection import get_async_session_factory
from awaithumans.server.services.notification_audit import (
    record_notification_failure,
)
from awaithumans.server.services.task_service import get_task
from awaithumans.utils.constants import (
    SLACK_ACTION_CLAIM_TASK,
    SLACK_ACTION_OPEN_REVIEW,
)

if TYPE_CHECKING:  # pragma: no cover
    from slack_sdk.web.async_client import AsyncWebClient
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("awaithumans.server.channels.slack.notifier")


async def notify_task(
    *,
    task_id: str,
    task_title: str,
    notify: list[str] | None,
    form_definition: dict[str, Any] | None,
) -> None:
    """Post the initial Slack message to every slack: route on the task."""
    routes = routes_for_channel(notify, "slack")
    if not routes:
        return

    form = _parse_form(form_definition)
    offenders = unsupported_fields(form, "slack") if form is not None else None
    fallback_text = f"New task: {task_title}"

    factory = get_async_session_factory()
    async with factory() as session:
        # Pull the task once so we can sign URLs for the assignee and
        # bind the handoff TTL to `task.timeout_at`. If the task was
        # deleted between the route handler and this background run
        # there's nothing to notify about — bail.
        try:
            task = await get_task(session, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify_task: task %s missing: %s", task_id, exc)
            return

        # Sign the URL for the resolved assignee when we have one.
        # Slack-only users (no email/password) have no other way through
        # the dashboard's login wall — the signed URL doubles as a
        # sign-in handoff (see core/slack_handoff.py).
        handoff = (
            HandoffParams(
                user_id=task.assigned_to_user_id,
                exp_unix=task_handoff_expiry(task.timeout_at),
            )
            if task.assigned_to_user_id and task.timeout_at
            else None
        )
        review_url = build_review_url(task_id=task_id, params=handoff)

        task_status = task.status.value if hasattr(task.status, "value") else str(task.status)

        for route in routes:
            # Broadcast: route target starts with `#` → posting to a
            # channel where anyone could pick it up. Swap the "Open in
            # Slack" button for "Claim this task" — first clicker wins.
            # DM targets (`@user` / `U123456`) stay on the direct-open
            # flow since the recipient is already implied.
            broadcast = _is_channel_target(route.target)

            blocks = open_review_message_blocks(
                task_id=task_id,
                task_title=task_title,
                review_url=review_url,
                open_button_action_id=SLACK_ACTION_OPEN_REVIEW,
                unsupported_fields=offenders if offenders else None,
                broadcast=broadcast,
                claim_button_action_id=SLACK_ACTION_CLAIM_TASK,
                task_metadata=task.task_metadata,
            )

            client = await _resolve_client(session, route)
            if client is None:
                logger.warning(
                    "Slack route %s → no client (identity=%s); skipping.",
                    route.target,
                    route.identity,
                )
                await record_notification_failure(
                    session,
                    task_id=task_id,
                    task_status=task_status,
                    channel="slack",
                    recipient=route.target,
                    reason="no_client",
                    message=(
                        "No Slack client available for this workspace. "
                        "Set AWAITHUMANS_SLACK_BOT_TOKEN, or install the "
                        "Slack app via the OAuth flow, or attach an "
                        "identity to the route (e.g. `slack+T123:#chan`)."
                    ),
                )
                continue

            # Resolve `@handle` / `email` to a real user_id before
            # posting. Slack's chat.postMessage doesn't do handle
            # resolution itself — sending to `@alice` silently fails.
            target = await resolve_slack_target(
                client=client,
                target=route.target,
                team_id=route.identity,
            )
            if target is None:
                logger.warning(
                    "Slack route %s → could not resolve to a user/channel; "
                    "skipping. Check the handle exists in this workspace.",
                    route.target,
                )
                await record_notification_failure(
                    session,
                    task_id=task_id,
                    task_status=task_status,
                    channel="slack",
                    recipient=route.target,
                    reason="target_not_found",
                    message=(
                        "Could not resolve this handle to a Slack user or "
                        "channel. Check the spelling and that the user is a "
                        "member of the workspace."
                    ),
                )
                continue
            try:
                resp = await client.chat_postMessage(
                    channel=target,
                    text=fallback_text,
                    blocks=blocks,
                )
                logger.info(
                    "Slack notification sent for task %s → %s%s%s",
                    task_id,
                    route.target,
                    f" (team={route.identity})" if route.identity else "",
                    " [broadcast]" if broadcast else "",
                )
                # Persist (channel, ts) so the post-completion updater
                # can rewrite the message to "Completed by X" later.
                # `resp["channel"]` is the resolved channel id even
                # when we posted to a user_id (Slack auto-opens an IM).
                await record_posted_message(
                    session,
                    task_id=task_id,
                    channel=resp.get("channel") or target,
                    ts=resp.get("ts") or "",
                    team_id=route.identity,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Slack notification failed for task %s → %s: %s",
                    task_id,
                    route.target,
                    exc,
                )
                await record_notification_failure(
                    session,
                    task_id=task_id,
                    task_status=task_status,
                    channel="slack",
                    recipient=route.target,
                    reason="post_message_error",
                    message=f"Slack API returned an error: {exc}",
                )

        await session.commit()


def _is_channel_target(target: str) -> bool:
    """`#channel` names are broadcasts; `@user` and raw user IDs are DMs.

    Slack uses `#` as the channel sigil across chat and the API. Raw
    channel IDs (`C01ABC234`) are also broadcasts; we detect those
    conservatively by checking the first char — `C` (public/private
    channel) or `G` (group DM). User IDs start with `U` or `W`.
    """
    if not target:
        return False
    if target.startswith("#"):
        return True
    return target.startswith(("C", "G"))


async def _resolve_client(session: AsyncSession, route: ChannelRoute) -> AsyncWebClient | None:
    if route.identity:
        # identity-suffixed route: pick exactly that workspace, no fallback.
        return await get_client_for_team(session, route.identity)
    return await get_default_client(session)


def _parse_form(form_definition: dict[str, Any] | None) -> FormDefinition | None:
    if not form_definition:
        return None
    try:
        return FormDefinition.model_validate(form_definition)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invalid form_definition on task: %s", exc)
        return None
