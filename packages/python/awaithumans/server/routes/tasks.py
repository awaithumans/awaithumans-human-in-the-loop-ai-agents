"""Task API routes — CRUD, long-poll, completion.

Route handlers only. All request/response models live in server/schemas.py.
Service exceptions (TaskNotFoundError, etc.) propagate to the centralized
handler in core/exceptions.py — no try/except in routes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.channels.email import notify_task as notify_task_email
from awaithumans.server.channels.slack import notify_task as notify_task_slack
from awaithumans.server.channels.slack.post_completion import (
    update_slack_messages_for_task,
)
from awaithumans.server.core.admin_auth import require_admin
from awaithumans.server.core.auth import SessionClaims
from awaithumans.server.core.embed_auth import get_embed_ctx
from awaithumans.server.core.task_auth import (
    caller_is_operator,
    caller_user_id,
    require_operator_or_admin,
    require_task_complete,
    require_task_read,
)
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import Task, TaskStatus, User
from awaithumans.server.schemas import (
    AuditEntryResponse,
    CompleteTaskRequest,
    CreateTaskRequest,
    PollResponse,
    TaskResponse,
)
from awaithumans.server.services.exceptions import TaskNotFoundError
from awaithumans.server.services.task_service import (
    cancel_task,
    claim_task,
    complete_task,
    create_task,
    delete_task,
    get_audit_trail,
    get_task,
    list_tasks,
)
from awaithumans.server.services.user_service import get_user
from awaithumans.server.services.webhook_dispatch import enqueue_completion_webhook
from awaithumans.utils.constants import TERMINAL_STATUSES_SET

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger("awaithumans.server.routes.tasks")


# ─── Helper ──────────────────────────────────────────────────────────────


def _user_display_name(user: User) -> str:
    """Same fallback chain the user-form picker uses, so the dashboard
    surfaces a Slack-only user's `@<slack_user_id>` instead of a raw
    row id. Used for both `assigned_to_*` and `completed_by_*` so
    Slack-only callers render the same way regardless of role."""
    if user.display_name:
        return user.display_name
    if user.email:
        return user.email
    if user.slack_user_id:
        return f"@{user.slack_user_id}"
    return user.id


async def _build_user_index(session: AsyncSession, tasks: Iterable[Task]) -> dict[str, User]:
    """Bulk-load Users referenced by the tasks (assignee + completer).

    One query per request instead of 2N — list_tasks_route in
    particular can return up to 200 rows. Empty when no task has
    either field set."""
    user_ids: set[str] = set()
    for t in tasks:
        if t.assigned_to_user_id:
            user_ids.add(t.assigned_to_user_id)
        if t.completed_by_user_id:
            user_ids.add(t.completed_by_user_id)
    if not user_ids:
        return {}
    result = await session.execute(select(User).where(User.id.in_(user_ids)))
    return {u.id: u for u in result.scalars().all()}


def _task_to_response(
    task: Task,
    *,
    redact: bool = False,
    assignee: User | None = None,
    completer: User | None = None,
) -> TaskResponse:
    """Convert a Task model to a TaskResponse, optionally redacting payload.

    When `assignee` / `completer` are provided, fills in display_name
    + slack_user_id so the dashboard can render Slack-only users
    correctly. Pass None for either when the task has no resolved
    directory user in that slot."""
    data = TaskResponse.model_validate(task)
    if redact and task.redact_payload:
        data.payload = {"_redacted": True}
    if assignee is not None:
        data.assigned_to_display_name = _user_display_name(assignee)
        data.assigned_to_slack_user_id = assignee.slack_user_id
    if completer is not None:
        data.completed_by_display_name = _user_display_name(completer)
        data.completed_by_slack_user_id = completer.slack_user_id
    return data


async def _task_to_response_with_lookup(
    session: AsyncSession,
    task: Task,
    *,
    redact: bool = False,
) -> TaskResponse:
    """Single-task convenience that does its own user lookups."""
    assignee: User | None = None
    completer: User | None = None
    if task.assigned_to_user_id:
        assignee = await get_user(session, task.assigned_to_user_id)
    if task.completed_by_user_id:
        completer = await get_user(session, task.completed_by_user_id)
    return _task_to_response(task, redact=redact, assignee=assignee, completer=completer)


# ─── Routes ──────────────────────────────────────────────────────────────


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task_route(
    body: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> TaskResponse:
    """Create a new HITL task (or return existing if idempotency key matches).

    Admin-only — agents call this with the `ADMIN_API_TOKEN` Bearer.
    Operators can also create tasks from the dashboard for ad-hoc
    workflows. Logged-in non-operators have no business creating tasks
    (the agent owns the contract that tasks are an output of code, not
    a UI button).

    Channel notifications fire in a FastAPI BackgroundTask *after* the
    response is sent, so a slow Slack API call never blocks task creation
    and a Slack outage never fails a successful task write.

    Idempotency replay: on a duplicate idempotency_key the existing task
    is returned with status 201 + an `Idempotent-Replayed: true` header,
    matching Stripe's idempotency contract. Clients that need to
    distinguish a fresh creation from a replay read the header.
    """
    task, was_newly_created = await create_task(
        session,
        task=body.task,
        payload=body.payload,
        payload_schema=body.payload_schema,
        response_schema=body.response_schema,
        form_definition=body.form_definition,
        timeout_seconds=body.timeout_seconds,
        idempotency_key=body.idempotency_key,
        assign_to=body.assign_to,
        notify=body.notify,
        verifier_config=body.verifier_config,
        redact_payload=body.redact_payload,
        callback_url=body.callback_url,
        task_metadata=body.task_metadata,
        initial_response=body.initial_response,
    )

    # Replay header: clients that care about distinguishing fresh
    # creation from idempotency hit (e.g. test harnesses, retry-aware
    # SDKs) read `Idempotent-Replayed: true`. Strict-HTTP-semantics
    # advocates would flip the status code from 201 → 200 here, but
    # Stripe (the model we cite for idempotency in task_service.py)
    # keeps the original status and signals via header — that pattern
    # breaks zero existing clients while still being informative.
    if not was_newly_created:
        response.headers["Idempotent-Replayed"] = "true"

    # Notifications fire only when this call actually CREATED the task.
    # An idempotency hit (same key as a previous task — common during
    # retries / agent restarts) returns the existing task; re-emailing
    # / re-Slacking on every retry was sending duplicate notifications
    # for the same logical work.
    if body.notify and was_newly_created:
        background_tasks.add_task(
            notify_task_slack,
            task_id=task.id,
            task_title=task.task,
            notify=body.notify,
            form_definition=task.form_definition,
        )
        background_tasks.add_task(
            notify_task_email,
            task_id=task.id,
            task_title=task.task,
            task_payload=None if task.redact_payload else task.payload,
            redact_payload=task.redact_payload,
            notify=body.notify,
            form_definition=task.form_definition,
        )

    return await _task_to_response_with_lookup(session, task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks_route(
    request: Request,
    status: TaskStatus | None = Query(None, description="Filter by status"),
    assigned_to: str | None = Query(
        None,
        description=(
            "Filter by assignee. Matches against the directory user's "
            "email (exact), Slack user ID (exact), or display name "
            "(case-insensitive substring), as well as the legacy "
            "assigned_to_email column for tasks routed by email "
            "before the user was provisioned."
        ),
    ),
    unassigned: bool = Query(
        False,
        description=(
            "If true, return only tasks where no assignee has been pinned "
            "(both user_id and email are null). Used by the dashboard to "
            "surface broadcast tasks needing Claim. Overrides assigned_to."
        ),
    ),
    terminal: bool = Query(
        False,
        description=(
            "If true, return only tasks in a terminal status (completed, "
            "timed_out, cancelled, verification_exhausted). Used by the "
            "Audit Log dashboard view. Combine with `status=` to filter "
            "within terminal (`status=` wins when both are set)."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[TaskResponse]:
    """List tasks. Operators / admin-bearer callers see everything;
    a logged-in non-operator (i.e., a regular reviewer) only sees the
    tasks routed to them.

    Without this scoping, a non-operator with a dashboard password
    could enumerate every task in the system (payloads, responses,
    audit data) — that defeats the routing model. Server-side filter
    is the authoritative gate; the dashboard's per-user view is a
    convenience, not a security control.

    Embed tokens are scoped to a single task and may NOT access the list
    endpoint — block them before any scoping logic."""
    if get_embed_ctx(request) is not None:
        raise HTTPException(status_code=403, detail="embed_token_cannot_list_tasks")
    if caller_is_operator(request) or getattr(request.state, "auth_admin_token", False):
        scoped_assigned_user_id: str | None = None
    else:
        # Non-operator session — force scope to the caller's own tasks
        # regardless of the `assigned_to` query param. Honouring the
        # client-supplied filter would let a non-operator pass any
        # email and read those tasks. Same goes for `unassigned=true`:
        # a reviewer asking to see "all unassigned" would expand their
        # visibility past their own queue.
        user_id = caller_user_id(request)
        if user_id is None:
            # Should be unreachable — middleware would have 401'd.
            return []
        scoped_assigned_user_id = user_id
        assigned_to = None
        unassigned = False

    tasks = await list_tasks(
        session,
        status=status,
        assigned_to_query=assigned_to,
        assigned_to_user_id=scoped_assigned_user_id,
        unassigned=unassigned,
        terminal=terminal,
        limit=limit,
        offset=offset,
    )
    users_by_id = await _build_user_index(session, tasks)
    return [
        _task_to_response(
            t,
            redact=True,
            assignee=users_by_id.get(t.assigned_to_user_id or ""),
            completer=users_by_id.get(t.completed_by_user_id or ""),
        )
        for t in tasks
    ]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task_route(
    task_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Get a single task by ID. Operator / admin / assignee / embed-token only."""
    embed_ctx = get_embed_ctx(request)
    if embed_ctx is not None:
        if embed_ctx.task_id != task_id:
            raise HTTPException(status_code=403, detail="task_outside_token_scope")
        task = await get_task(session, task_id)
        return await _task_to_response_with_lookup(session, task)
    # Cookie / admin-bearer path.
    task = await get_task(session, task_id)
    require_task_read(request, task)
    return await _task_to_response_with_lookup(session, task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task_route(
    task_id: str,
    body: CompleteTaskRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Complete a task with the human's response.

    First-writer-wins: if the task is already terminal, returns 409 Conflict
    (handled by the centralized ServiceError handler).

    If the request didn't explicitly supply `completed_by_email` (the
    dashboard form doesn't — why would the browser lie about who it is)
    we read the logged-in user from the session cookie and record their
    email. That's the correct attribution: client can't fake it, server
    authoritatively stamps who clicked submit.
    """
    # Embed-bearer path: authorise by task_id scope, skip cookie auth.
    embed_ctx = get_embed_ctx(request)
    if embed_ctx is not None:
        if embed_ctx.task_id != task_id:
            raise HTTPException(status_code=403, detail="task_outside_token_scope")
        task = await complete_task(
            session,
            task_id=task_id,
            response=body.response,
            completed_by_email=body.completed_by_email,
            completed_by_user_id=None,
            completed_via_channel="embed",
            channel="embed",
            embed_sub=embed_ctx.sub,
            embed_jti=embed_ctx.jti,
        )
        if task.status in TERMINAL_STATUSES_SET:
            await enqueue_completion_webhook(session, task)
            background_tasks.add_task(update_slack_messages_for_task, task.id)
        return await _task_to_response_with_lookup(session, task)

    # Authorise BEFORE running the verifier — a non-assignee
    # submitting via the dashboard form would otherwise burn an
    # attempt and ship the (potentially sensitive) payload to the LLM.
    existing = await get_task(session, task_id)
    require_task_complete(request, existing)

    completer_email = body.completed_by_email
    if not completer_email:
        completer_email = await _session_user_email(request, session)

    # Stamp the user_id from the session cookie too — for Slack-only
    # users (no email column), email-only attribution leaves the audit
    # trail showing "—" and the operator can't tell who pressed submit.
    completer_user_id = caller_user_id(request)

    task = await complete_task(
        session,
        task_id=task_id,
        response=body.response,
        completed_by_email=completer_email,
        completed_by_user_id=completer_user_id,
        completed_via_channel=body.completed_via_channel,
    )

    # Enqueue the outbound webhook (the actual POST happens via the
    # background dispatcher with retry-and-backoff). REJECTED is
    # non-terminal, the agent shouldn't get a "complete" callback for
    # a verifier-rejected attempt — only enqueue on a real terminal.
    # Enqueue inline so the row is committed in the same unit of work
    # as the task transition; if the request fails after this we don't
    # want a delivery for state that never landed.
    if task.status in TERMINAL_STATUSES_SET:
        await enqueue_completion_webhook(session, task)

    # Replace the original Slack message so the recipient stops
    # seeing "open" action buttons after they (or someone else) has
    # already completed the task. Best-effort, runs after response.
    if task.status in TERMINAL_STATUSES_SET:
        background_tasks.add_task(update_slack_messages_for_task, task.id)

    return await _task_to_response_with_lookup(session, task)


async def _session_user_email(request: Request, session: AsyncSession) -> str | None:
    """Look up the logged-in user's email from the session cookie claims.

    Returns None when the caller isn't using a cookie (admin bearer
    token, magic-link email flow, etc.) — those callers already supply
    `completed_by_email` themselves through channel-specific paths.
    """
    claims = getattr(request.state, "auth_claims", None)
    if not isinstance(claims, SessionClaims):
        return None
    user = await get_user(session, claims.user_id)
    return user.email if user else None


@router.post("/{task_id}/claim", response_model=TaskResponse)
async def claim_task_route(
    task_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Claim an unassigned task for the logged-in operator.

    Mirrors the Slack "Claim" button and the email-handoff auto-claim
    in shape — first-writer-wins via the `claim_task` service. Used
    by the dashboard so an operator can become the assignee on a
    broadcast (`notify=`) task or a task created without `assign_to=`.
    Once assigned, the task page renders the response form so the
    operator can submit Approve / Reject.

    Caller MUST have a cookie session (operator-or-admin in our
    role model). Pure admin-bearer is rejected — that token belongs
    to the AI agent, not a human operator, so there's no user_id to
    pin as the assignee. If you find yourself wanting to "admin-claim"
    a task, log in to the dashboard with your operator account first.
    """
    require_operator_or_admin(request)

    user_id = caller_user_id(request)
    if user_id is None:
        # Admin bearer with no session → no human identity to claim as.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=(
                "Claim requires a logged-in operator session. "
                "Admin bearer tokens have no user identity to assign."
            ),
        )

    claimer_email = await _session_user_email(request, session)
    task = await claim_task(
        session,
        task_id=task_id,
        user_id=user_id,
        user_email=claimer_email,
        claimed_via_channel="dashboard",
    )
    return await _task_to_response_with_lookup(session, task)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task_route(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Cancel a task. Operator / admin only.

    The agent is the canonical caller (admin bearer); operators get
    the dashboard "Cancel" button. Non-operator humans don't get to
    cancel — that would let a reviewer kill a task they were assigned
    to before completing it, which the agent won't expect."""
    require_operator_or_admin(request)
    task = await cancel_task(session, task_id)

    # Cancellation is also a terminal transition the durable adapter
    # cares about — without the webhook, a Temporal workflow would
    # sit waiting for a signal that never comes. Enqueueing rides the
    # same DB transaction as the cancel so we can't end up with one
    # without the other.
    await enqueue_completion_webhook(session, task)

    # Mirror the completion path: replace the now-stale "open" Slack
    # messages with a "Cancelled" surface so the operator who got
    # DM'd doesn't try to fill the form anyway.
    background_tasks.add_task(update_slack_messages_for_task, task.id)

    return await _task_to_response_with_lookup(session, task)


@router.delete(
    "/{task_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_admin)],
)
async def delete_task_route(
    task_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Hard delete a task. Operator-only surface.

    Unlike `/cancel` (which keeps the row with status=CANCELLED),
    this removes the row entirely. Audit entries aren't cascaded —
    they persist as a historical record of what happened, orphaned.
    """
    if not await delete_task(session, task_id):
        raise TaskNotFoundError(task_id)
    return Response(status_code=204)


@router.get("/{task_id}/poll", response_model=PollResponse)
async def poll_task_route(
    task_id: str,
    request: Request,
    timeout: int = Query(25, ge=1, le=30, description="Long-poll timeout in seconds"),
) -> PollResponse:
    """Long-poll for task completion. Operator / admin / assignee only.

    Holds the HTTP connection open for up to `timeout` seconds (default 25, max 30).
    Returns immediately if the task is already in a terminal state.
    If the task is still pending after the timeout, returns the current status
    so the client can reconnect.

    Note: does NOT hold a DB session open during the wait — acquires a fresh
    short-lived session for each 1-second check to avoid exhausting the pool.
    """
    from awaithumans.server.db.connection import get_async_session_factory

    factory = get_async_session_factory()

    # Check current state immediately
    async with factory() as session:
        task = await get_task(session, task_id)

        # Authorise once on the initial read; the assignee/operator
        # status of the caller doesn't change inside the long-poll
        # window, so re-checking each second would just be busywork.
        require_task_read(request, task)

        if task.status in TERMINAL_STATUSES_SET:
            return PollResponse(
                status=task.status.value,
                response=task.response,
                completed_at=task.completed_at,
                timed_out_at=task.timed_out_at,
            )

    # Long-poll: check every 1 second with a fresh session each time
    elapsed = 0
    last_status = task.status.value
    while elapsed < timeout:
        await asyncio.sleep(1)
        elapsed += 1

        async with factory() as session:
            task = await get_task(session, task_id)

            if task.status in TERMINAL_STATUSES_SET:
                return PollResponse(
                    status=task.status.value,
                    response=task.response,
                    completed_at=task.completed_at,
                    timed_out_at=task.timed_out_at,
                )
            last_status = task.status.value

    # Timeout — return current status so client can reconnect
    return PollResponse(
        status=last_status,
        response=None,
        completed_at=None,
        timed_out_at=None,
    )


@router.get("/{task_id}/audit", response_model=list[AuditEntryResponse])
async def get_audit_trail_route(
    task_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[AuditEntryResponse]:
    """Get the full audit trail for a task. Admin / operator / assignee.

    Same allow-list as `GET /tasks/{id}` — the assignee already sees
    the task's payload, response, and verifier_result via the parent
    fetch, so withholding their own task's audit trail just broke the
    /task page (any 4xx on this endpoint blanked the whole view) without
    actually protecting any data they couldn't already see.

    Cross-assignee enumeration is still blocked: a non-operator can
    only read tasks where `assigned_to_user_id == claims.user_id`.
    """
    task = await get_task(session, task_id)  # raises TaskNotFoundError
    require_task_read(request, task)
    entries = await get_audit_trail(session, task.id)
    return [AuditEntryResponse.model_validate(e) for e in entries]
