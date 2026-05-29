"""Slack interactivity webhook — `POST /interactions`.

Slack POSTs here for every button click and modal submission. The body
is `application/x-www-form-urlencoded` with a single `payload` field
whose value is a JSON string. Two payload shapes are handled:

- `block_actions`: user clicked the "Open in Slack" button on the
  initial message → open a modal via `views.open`.
- `view_submission`: user submitted the modal → coerce values and
  complete the task.

Signature verification uses the raw request body (not the parsed form).
The route reads the body twice: once as bytes for HMAC, once as form
data for the payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from slack_sdk.errors import SlackApiError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # pragma: no cover
    from slack_sdk.web.async_client import AsyncWebClient

from awaithumans.forms import FormDefinition
from awaithumans.server.channels.slack.blocks import (
    UnrenderableInSlackError,
    claimed_message_blocks,
    form_to_modal,
)
from awaithumans.server.channels.slack.client import get_client_for_team
from awaithumans.server.channels.slack.coerce import slack_values_to_response
from awaithumans.server.channels.slack.handoff_url import (
    build_review_url,
    task_handoff_expiry,
)
from awaithumans.server.channels.slack.handoff_url_types import HandoffParams
from awaithumans.server.channels.slack.post_completion import (
    update_slack_messages_for_task,
)
from awaithumans.server.channels.slack.signing import verify_signature
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import Task
from awaithumans.server.services.exceptions import (
    TaskAlreadyClaimedError,
    TaskAlreadyTerminalError,
)
from awaithumans.server.services.task_service import (
    claim_task,
    complete_task,
    get_task,
)
from awaithumans.server.services.user_service import (
    get_user,
    get_user_by_slack,
    link_slack_identity_by_email,
)
from awaithumans.utils.constants import (
    SLACK_ACTION_CLAIM_TASK,
    SLACK_ACTION_OPEN_REVIEW,
    SLACK_RESPONSE_URL_TIMEOUT_SECONDS,
)

router = APIRouter()
logger = logging.getLogger("awaithumans.server.routes.slack.interactions")


@router.post("/interactions")
async def slack_interactions(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    body = await request.body()

    if not settings.SLACK_SIGNING_SECRET:
        logger.error("Slack interactivity received but SLACK_SIGNING_SECRET unset.")
        raise HTTPException(status_code=503, detail="Slack integration not configured.")

    if not verify_signature(
        body=body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
        signing_secret=settings.SLACK_SIGNING_SECRET,
    ):
        logger.warning("Slack interactivity: signature verification failed.")
        raise HTTPException(status_code=401, detail="Invalid Slack signature.")

    form = await request.form()
    raw_payload = form.get("payload")
    if not isinstance(raw_payload, str):
        raise HTTPException(status_code=400, detail="Missing payload.")

    payload = json.loads(raw_payload)
    payload_type = payload.get("type")

    if payload_type == "block_actions":
        await _handle_block_actions(payload, session)
        return None

    if payload_type == "view_submission":
        return await _handle_view_submission(payload, session)

    logger.info("Slack interactivity: ignoring payload type %s", payload_type)
    return None


# ─── block_actions — open the review modal ──────────────────────────────


async def _handle_block_actions(
    payload: dict[str, Any],
    session: AsyncSession,
) -> None:
    actions = payload.get("actions") or []

    # Claim-first path: broadcast-to-channel messages show a "Claim"
    # button. First clicker wins atomically, then the modal opens for
    # them. Claim has priority over open-review so a broadcast message
    # with both buttons is disambiguated.
    claim_action = next(
        (a for a in actions if a.get("action_id") == SLACK_ACTION_CLAIM_TASK),
        None,
    )
    if claim_action:
        await _handle_claim(payload, claim_action, session)
        return

    open_action = next(
        (a for a in actions if a.get("action_id") == SLACK_ACTION_OPEN_REVIEW),
        None,
    )
    if not open_action:
        return  # Some other button — dashboard link-out etc. — no server work.

    task_id = open_action.get("value")
    trigger_id = payload.get("trigger_id")
    team = payload.get("team") or {}
    team_id = team.get("id")
    user = payload.get("user") or {}
    slack_user_id = user.get("id")
    response_url = payload.get("response_url")
    channel = (payload.get("channel") or {}).get("id")

    if not task_id or not trigger_id:
        logger.warning("block_actions: missing task_id or trigger_id.")
        return

    # Authorise the click before we open a modal that completes the
    # task. Without this, anyone in a shared channel who saw the
    # message could open the form and submit on behalf of the actual
    # assignee. The claim path doesn't need this — it's "first click
    # wins by design" — but the direct-DM ("Open in Slack") path does.
    task = await get_task(session, task_id)
    authorised, why_not = await _slack_user_can_act_on_task(
        session=session,
        task=task,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if not authorised:
        client = await get_client_for_team(session, team_id)
        if client is not None and slack_user_id:
            await _ephemeral_reply(
                client=client,
                channel=channel,
                user_id=slack_user_id,
                response_url=response_url,
                text=why_not,
            )
        return

    await _open_modal_for_task(
        session=session,
        task_id=task_id,
        trigger_id=trigger_id,
        team_id=team_id,
    )


async def _resolve_slack_user_with_auto_link(
    *,
    session: AsyncSession,
    team_id: str,
    slack_user_id: str,
) -> tuple[Any | None, str | None]:
    """Look up the directory user for a Slack identity, with auto-link fallback.

    Path 1 (fast): direct (team_id, slack_user_id) hit. Returns the user.

    Path 2 (auto-link, first click only): if no direct hit, call
    Slack's users.info API for the clicker's email and atomically bind
    it to a matching directory user (where slack_user_id IS NULL).
    Subsequent clicks hit path 1.

    Path 3 (refuse): no email exposed by Slack (missing
    ``users:read.email`` scope), or no directory user matches, or the
    match already has a different Slack identity bound. Returns
    ``(None, fallback_error_text)`` so the caller posts an ephemeral
    refusal with the right hint.

    See #144.
    """
    direct = await get_user_by_slack(session, slack_team_id=team_id, slack_user_id=slack_user_id)
    if direct is not None and direct.active:
        return direct, None

    client = await get_client_for_team(session, team_id)
    if client is None:
        return None, (
            "You're not in this server's user directory. Ask your "
            "operator to add you via Settings → Users."
        )

    try:
        info = await client.users_info(user=slack_user_id)
    except SlackApiError as exc:
        logger.warning(
            "auto-link: users_info failed for %s/%s: %s",
            team_id,
            slack_user_id,
            exc.response.get("error", "unknown"),
        )
        return None, (
            "You're not in this server's user directory. Ask your "
            "operator to add you via Settings → Users."
        )

    profile = (info.data.get("user") or {}).get("profile") or {}
    email = profile.get("email")
    if not email:
        return None, (
            "You're not in this server's user directory. The Slack "
            "app couldn't read your email (the `users:read.email` "
            "scope may be missing) so auto-linking isn't possible — "
            "ask your operator to add you via Settings → Users."
        )

    linked = await link_slack_identity_by_email(
        session,
        email=email,
        slack_team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if linked is None:
        return None, (
            f"You're not in this server's user directory. Your Slack "
            f"email ({email}) doesn't match any registered operator. "
            "Ask your operator to add you via Settings → Users."
        )
    if not linked.active:
        return None, (
            "Your operator account is deactivated. Ask your operator "
            "to re-activate it via Settings → Users."
        )

    logger.info(
        "slack_identity_linked user=%s team=%s slack_user=%s via=first_click",
        linked.id,
        team_id,
        slack_user_id,
    )
    return linked, None


async def _slack_user_can_act_on_task(
    *,
    session: AsyncSession,
    task: Task,
    team_id: str | None,
    slack_user_id: str | None,
) -> tuple[bool, str]:
    """Check whether a Slack user is authorised to open / submit a task.

    Returns (authorised, reason_when_not). A Slack user is authorised
    when:

      - they're in the directory AND active, AND
      - they're either the task's assignee, OR an operator.

    Anyone else gets blocked with a human-readable reason so the
    ephemeral reply tells them why. The resolver auto-links a Slack
    identity to a matching directory user by email on first click,
    avoiding the "ask your operator to add you" wall (#144).
    """
    if not team_id or not slack_user_id:
        return False, "Missing Slack identity in the interaction payload."

    directory_user, error_text = await _resolve_slack_user_with_auto_link(
        session=session, team_id=team_id, slack_user_id=slack_user_id
    )
    if directory_user is None:
        return False, error_text or "Authorisation check failed."

    if directory_user.is_operator:
        return True, ""
    if task.assigned_to_user_id == directory_user.id:
        return True, ""

    return False, (
        "This task isn't assigned to you. Operators can review any "
        "task from the dashboard; reviewers can only act on the "
        "tasks routed to them."
    )


async def _open_modal_for_task(
    *,
    session: AsyncSession,
    task_id: str,
    trigger_id: str,
    team_id: str | None,
) -> None:
    """Load the task, build the modal, open it via `views.open`.

    Shared between the direct "Open in Slack" button (DM flow) and the
    post-claim modal pop (channel broadcast flow).
    """
    task = await get_task(session, task_id)
    if task.form_definition is None:
        logger.warning("Task %s has no form_definition; cannot open modal.", task_id)
        return

    try:
        form = FormDefinition.model_validate(task.form_definition)
        view = form_to_modal(
            form=form,
            task_id=task.id,
            task_title=task.task,
            task_payload=task.payload,
            redact_payload=task.redact_payload,
            task_metadata=task.task_metadata,
        )
    except UnrenderableInSlackError as exc:
        logger.warning("Task %s not Slack-renderable: %s", task_id, exc)
        return

    client = await get_client_for_team(session, team_id)
    if client is None:
        logger.error(
            "views.open aborted: no client for team_id=%s (not installed?).",
            team_id,
        )
        return

    await client.views_open(trigger_id=trigger_id, view=view)


async def _handle_claim(
    payload: dict[str, Any],
    action: dict[str, Any],
    session: AsyncSession,
) -> None:
    """Handle a "Claim this task" click from a broadcast channel message.

    Flow:
      1. Resolve the Slack user to a directory user (by team_id + slack_user_id).
         Users who aren't in the directory get an ephemeral "ask your operator
         to add you" reply — enforces directory hygiene so claims correlate
         cleanly with the routing model.
      2. Atomic claim on the task — first click wins. Second click gets an
         ephemeral "already claimed by ..." reply.
      3. Update the channel message to show who claimed it (hides the
         button for everyone else).
      4. Open the response modal for the claimer.
    """
    task_id = action.get("value")
    trigger_id = payload.get("trigger_id")
    team = payload.get("team") or {}
    team_id = team.get("id")
    user = payload.get("user") or {}
    slack_user_id = user.get("id")
    slack_username = user.get("username") or user.get("name")
    response_url = payload.get("response_url")
    channel = (payload.get("channel") or {}).get("id")
    message = payload.get("message") or {}
    message_ts = message.get("ts")

    if not task_id or not trigger_id or not team_id or not slack_user_id:
        logger.warning(
            "claim: missing field (task_id=%s, trigger_id=%s, team=%s, user=%s)",
            task_id,
            trigger_id,
            team_id,
            slack_user_id,
        )
        return

    client = await get_client_for_team(session, team_id)
    if client is None:
        logger.error("claim: no client for team_id=%s", team_id)
        return

    directory_user, error_text = await _resolve_slack_user_with_auto_link(
        session=session, team_id=team_id, slack_user_id=slack_user_id
    )
    if directory_user is None:
        await _ephemeral_reply(
            client=client,
            channel=channel,
            user_id=slack_user_id,
            response_url=response_url,
            text=error_text or "Authorisation check failed.",
        )
        return

    try:
        task = await claim_task(
            session,
            task_id=task_id,
            user_id=directory_user.id,
            user_email=directory_user.email,
            claimed_via_channel="slack",
        )
    except TaskAlreadyClaimedError as exc:
        claimer_display = await _display_for_user_id(session, exc.claimed_by_user_id)
        await _ephemeral_reply(
            client=client,
            channel=channel,
            user_id=slack_user_id,
            response_url=response_url,
            text=f"Already claimed by {claimer_display}.",
        )
        return
    except TaskAlreadyTerminalError:
        await _ephemeral_reply(
            client=client,
            channel=channel,
            user_id=slack_user_id,
            response_url=response_url,
            text="This task is already completed or cancelled.",
        )
        return

    # Message update: swap the card for a "Claimed by X" state so the
    # button vanishes for the rest of the channel. Best-effort — if
    # chat.update fails (lost permissions, message deleted) we still
    # pop the modal for the claimer.
    claimer_display = (
        f"<@{slack_user_id}>"
        if slack_user_id
        else directory_user.display_name or directory_user.email or "a user"
    )
    # Sign the post-claim URL for the claimer so a Slack-only user
    # can click straight through into the dashboard. Other channel
    # members clicking the button will still be challenged for a
    # password — the link is bound to the claimer specifically.
    handoff = (
        HandoffParams(
            user_id=directory_user.id,
            exp_unix=task_handoff_expiry(task.timeout_at),
        )
        if directory_user and task.timeout_at
        else None
    )
    review_url = build_review_url(task_id=task.id, params=handoff)
    if channel and message_ts:
        try:
            await client.chat_update(
                channel=channel,
                ts=message_ts,
                text=f"Claimed by {slack_username or 'a user'}: {task.task}",
                blocks=claimed_message_blocks(
                    task_title=task.task,
                    review_url=review_url,
                    claimed_by_display=claimer_display,
                ),
            )
        except SlackApiError as exc:
            # Slack-side failures (lost permissions, message deleted,
            # missing scope) are best-effort — the modal still opens
            # for the claimer so they can complete the task. Anything
            # NOT a SlackApiError is a real bug and should propagate.
            logger.warning("chat.update after claim failed: %s", exc.response.get("error", exc))

    # Pop the modal for the claimer so they can complete it immediately.
    await _open_modal_for_task(
        session=session,
        task_id=task.id,
        trigger_id=trigger_id,
        team_id=team_id,
    )


async def _display_for_user_id(session: AsyncSession, user_id: str | None) -> str:
    """Human-readable label for the user who won a claim race."""
    if not user_id:
        return "another user"
    user = await get_user(session, user_id)
    if user is None:
        return "another user"
    if user.slack_user_id:
        return f"<@{user.slack_user_id}>"
    return user.display_name or user.email or "another user"


async def _ephemeral_reply(
    *,
    client: AsyncWebClient,
    channel: str | None,
    user_id: str,
    response_url: str | None,
    text: str,
) -> None:
    """Post an ephemeral message to the clicker.

    Slack's interaction payloads include a short-lived `response_url`
    that accepts a plain JSON POST from anywhere — no bot token or
    channel membership needed. We hit it directly with `httpx`
    (already a runtime dep) because `AsyncWebClient.api_call` only
    targets `https://slack.com/api/<method>` and can't override the
    URL. Falls back to `chat.postEphemeral` (requires `chat:write`
    scope + bot membership in the channel) for edges where the
    response_url isn't present.
    """
    import httpx

    if response_url:
        try:
            async with httpx.AsyncClient(timeout=SLACK_RESPONSE_URL_TIMEOUT_SECONDS) as http:
                resp = await http.post(
                    response_url,
                    json={"response_type": "ephemeral", "text": text},
                )
                resp.raise_for_status()
            return
        except httpx.HTTPError as exc:
            # response_url is a transient signed URL Slack provides;
            # network issues / 4xx after expiry / Slack-side hiccups
            # are expected operational noise. We catch httpx errors
            # specifically so genuine bugs (TypeError, etc.) propagate.
            logger.warning("ephemeral via response_url failed: %s", exc)

    if channel:
        try:
            await client.chat_postEphemeral(channel=channel, user=user_id, text=text)
        except SlackApiError as exc:
            # `not_in_channel` / `missing_scope` / token revoked all
            # surface here; the route still completes successfully
            # (the ephemeral was best-effort).
            logger.warning(
                "chat.postEphemeral failed: %s",
                exc.response.get("error", exc),
            )


# ─── view_submission — complete the task ────────────────────────────────


async def _handle_view_submission(
    payload: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    view = payload.get("view") or {}
    task_id = view.get("private_metadata")
    if not task_id:
        raise HTTPException(status_code=400, detail="Missing task_id in modal metadata.")

    user = payload.get("user") or {}
    team = payload.get("team") or {}
    slack_user_id = user.get("id")
    team_id = team.get("id")

    task = await get_task(session, task_id)
    if task.form_definition is None:
        raise HTTPException(
            status_code=400,
            detail="Task has no form_definition; cannot coerce submission.",
        )

    # Authorise the submitter. Without this, anyone with a workspace
    # session who could trigger the modal (or replay a captured
    # `private_metadata` task_id) could complete tasks they were never
    # assigned to. Slack returns the rejection inline as a modal
    # response_action so the user sees a clear message; the task is
    # not touched.
    authorised, why_not = await _slack_user_can_act_on_task(
        session=session,
        task=task,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    if not authorised:
        return {
            "response_action": "errors",
            "errors": {"awaithumans:_auth": why_not},
        }

    # Record the directory email, not the Slack-supplied `username`
    # which is just the @handle. Looking up via the directory makes
    # `completed_by_email` consistent across channels (Slack
    # completions look the same as dashboard ones in the audit log).
    # `completed_by_user_id` is also stamped so a Slack-only user
    # (no email) is still identifiable in the audit log.
    directory_user = await get_user_by_slack(
        session, slack_team_id=team_id, slack_user_id=slack_user_id
    )
    completer_email = directory_user.email if directory_user else None
    completer_user_id = directory_user.id if directory_user else None

    form = FormDefinition.model_validate(task.form_definition)
    response = slack_values_to_response(form, view.get("state") or {})

    await complete_task(
        session,
        task_id=task_id,
        response=response,
        completed_by_email=completer_email,
        completed_by_user_id=completer_user_id,
        completed_via_channel="slack",
    )

    # Replace the original "Approve / Reject" message with a
    # "Completed by X" surface. We schedule it as a fire-and-forget
    # task because Slack expects the view_submission response within
    # 3s — a slow chat.update would push us over that and Slack
    # would re-deliver the submission, double-completing the task.
    asyncio.create_task(update_slack_messages_for_task(task_id))

    # Empty response closes the modal successfully.
    return {}
