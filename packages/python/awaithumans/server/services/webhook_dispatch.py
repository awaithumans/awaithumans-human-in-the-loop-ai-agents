"""Outbound webhook dispatch for tasks with `callback_url`.

When an agent creates a task with `callback_url=...`, it's saying
"don't make me long-poll — push me when something changes." On every
terminal-status transition (COMPLETED, TIMED_OUT, CANCELLED,
VERIFICATION_EXHAUSTED) the server writes a `WebhookDelivery` row
and lets a background scheduler handle delivery with retry-and-backoff.

This is the foundation the durable-execution adapters (Temporal,
LangGraph) ride on: the user's web server registers a small handler
that verifies the HMAC, extracts the workflow identity, and signals
the workflow to resume. Without retry, a momentary outage on the
receiver leaves the workflow waiting forever for a signal that was
fired-and-lost.

Wire format (unchanged from before — this module only changes
WHEN we send, not WHAT we send):

    POST {callback_url}
    Content-Type: application/json
    X-Awaithumans-Signature: sha256=<hex>
    X-Awaithumans-Task-Id: <task_id>

    {
      "task_id": "...",
      "idempotency_key": "...",
      "status": "completed" | "timed_out" | "cancelled" | "verification_exhausted",
      "response": {...} | null,
      "completed_at": ISO8601 | null,
      "completed_by_email": str | null,
      "completed_via_channel": str | null,
      "verification_attempt": int
    }

Receivers should:
  1. Read the raw body as bytes.
  2. Recompute HMAC-SHA256(body) with their shared secret.
  3. Compare-digest against the `X-Awaithumans-Signature` header.
  4. Only then trust the JSON.

Delivery semantics:
  - At-least-once until either success or the 3-day age cap is hit.
  - Body bytes are persisted at enqueue time so retries re-send the
    exact same wire payload (signature stays valid).
  - Backoff schedule lives in `WEBHOOK_RETRY_BACKOFF_SECONDS`. After
    that schedule is exhausted the row is marked ABANDONED, an event
    is logged, and an admin can redrive via `/api/admin/webhook-
    deliveries/{id}/redeliver`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import (
    Task,
    WebhookDelivery,
    WebhookDeliveryStatus,
)
from awaithumans.utils.constants import (
    WEBHOOK_DELIVERY_TIMEOUT_SECONDS,
    WEBHOOK_RETRY_BACKOFF_SECONDS,
    WEBHOOK_RETRY_MAX_AGE_SECONDS,
    WEBHOOK_SIGNATURE_HEADER,
)

# HMAC primitives moved to a server-package-free module (PR #71) so
# the durable adapters can verify callbacks without pulling in
# FastAPI / SQLModel / etc. We re-export the same names here for
# backward compat — every existing caller still does
# `from awaithumans.server.services.webhook_dispatch import sign_body`
# and that continues to work.
from awaithumans.utils.webhook_signing import sign_body, verify_signature  # noqa: F401

logger = logging.getLogger("awaithumans.server.services.webhook_dispatch")


def _build_payload(task: Task) -> dict[str, Any]:
    """The JSON body the receiver gets. Designed to be self-contained
    so the receiver doesn't need a second round-trip to figure out
    what happened."""
    return {
        "task_id": task.id,
        "idempotency_key": task.idempotency_key,
        "status": task.status.value,
        "response": task.response,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "timed_out_at": task.timed_out_at.isoformat() if task.timed_out_at else None,
        "completed_by_email": task.completed_by_email,
        "completed_via_channel": task.completed_via_channel,
        "verification_attempt": task.verification_attempt,
    }


def backoff_delay(attempt_count: int) -> int:
    """Seconds to wait before retrying after the Nth failed attempt.

    `attempt_count` is the number of attempts that have ALREADY been
    made (including the one that just failed). Returns the last entry
    of the schedule for any over-shoot — the dispatcher's age cap is
    what actually stops retries; the schedule just controls cadence.
    """
    if attempt_count < 1:
        return WEBHOOK_RETRY_BACKOFF_SECONDS[0]
    idx = min(attempt_count - 1, len(WEBHOOK_RETRY_BACKOFF_SECONDS) - 1)
    return WEBHOOK_RETRY_BACKOFF_SECONDS[idx]


async def enqueue_completion_webhook(session: AsyncSession, task: Task) -> None:
    """Persist a `WebhookDelivery` row for the task's terminal state.

    Call right after the service-layer transition that flipped the
    task to a terminal status (the service has already committed by
    that point — see `complete_task` / `cancel_task` / `timeout_task`).
    The window between those commits and this one is "two consecutive
    `await` calls" — tiny but non-zero; if the process crashes there
    the task is terminal but no delivery is enqueued. The agent's
    long-poll fallback recovers, and a future refactor that pulls the
    enqueue inside the service's transaction can close the gap.

    No-op for tasks without `callback_url`. Multiple terminal
    transitions on the same task (e.g. re-cancellation, theoretical
    edge cases) each enqueue a row — by design; the receiver is
    HMAC-authenticated and idempotency_key lets it de-duplicate if
    it cares.
    """
    if not task.callback_url:
        return

    body = json.dumps(_build_payload(task), separators=(",", ":")).encode()
    delivery = WebhookDelivery(
        task_id=task.id,
        url=task.callback_url,
        body=body,
        signature=sign_body(body),
        status=WebhookDeliveryStatus.PENDING,
        # next_attempt_at defaults to utc_now — picked up on the very
        # next scheduler tick, so latency for a healthy receiver is
        # bounded by WEBHOOK_SCHEDULER_INTERVAL_SECONDS.
    )
    session.add(delivery)
    await session.commit()


async def _attempt_post(
    delivery: WebhookDelivery,
) -> tuple[bool, int | None, str | None]:
    """One outbound attempt. Returns `(success, status_code, error)`.

    Pure function over the delivery row — does not mutate state.
    Caller updates the row based on the result."""
    headers = {
        "Content-Type": "application/json",
        WEBHOOK_SIGNATURE_HEADER: delivery.signature,
        "X-Awaithumans-Task-Id": delivery.task_id,
    }
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_DELIVERY_TIMEOUT_SECONDS) as client:
            resp = await client.post(delivery.url, content=delivery.body, headers=headers)
            if resp.status_code >= 400:
                return False, resp.status_code, f"HTTP {resp.status_code}"
            return True, resp.status_code, None
    except httpx.HTTPError as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


async def _claim_due_deliveries(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[WebhookDelivery]:
    """Find rows that are due and pin each one's `next_attempt_at`
    forward by the worst-case scheduler tick so a second scheduler
    instance starting up at the same time can't pick them up too.

    Concurrency model: the dispatcher is normally a single asyncio
    task per process. The pin-and-fetch dance below makes it safe
    for accidental duplication (operator running two `awaithumans
    dev` processes against the same DB, or a future move to multi-
    worker deployments). Postgres takes a row-level lock on the
    UPDATE; SQLite serialises writes globally.
    """
    # Pick which IDs would be due. The query stays cheap because the
    # composite (status, next_attempt_at) index covers it.
    rows = await session.execute(
        select(WebhookDelivery.id)
        .where(WebhookDelivery.status == WebhookDeliveryStatus.PENDING)
        .where(WebhookDelivery.next_attempt_at <= now)
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    ids = [row[0] for row in rows.all()]
    if not ids:
        return []

    # Pin them: bump next_attempt_at to now + claim window so a
    # competing scheduler doesn't race in. We use a short window
    # (10s — same as the per-attempt HTTP timeout) so a crashed
    # dispatcher's claims expire quickly and another instance picks
    # them up.
    #
    # synchronize_session=False keeps SQLAlchemy from re-evaluating
    # the WHERE clause in Python after the UPDATE — that path
    # compares naive vs. aware datetimes (SQLite stores datetimes
    # without tz) and explodes. We don't need session-cache sync
    # here; we re-fetch fresh rows below.
    pinned_until = now + timedelta(seconds=WEBHOOK_DELIVERY_TIMEOUT_SECONDS)
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id.in_(ids))
        .where(WebhookDelivery.status == WebhookDeliveryStatus.PENDING)
        .where(WebhookDelivery.next_attempt_at <= now)
        .values(next_attempt_at=pinned_until, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.commit()

    # Re-fetch full rows. Anyone who lost the race is excluded by
    # the WHERE — their next_attempt_at moved past `now` already.
    fetched = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.id.in_(ids))
        .where(WebhookDelivery.next_attempt_at == pinned_until)
    )
    return list(fetched.scalars().all())


def _ensure_utc(dt: datetime) -> datetime:
    """Coerce a (possibly naive) DB-loaded datetime to UTC-aware.

    Same convention as `schemas/_datetime.py:utc_iso`: SQLite drops
    tzinfo on the way out, but our write path always uses
    `datetime.now(timezone.utc)`, so a naive value is UTC by
    construction. Comparison against an aware `now` would otherwise
    raise `TypeError: can't compare offset-naive and offset-aware`.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _too_old(delivery: WebhookDelivery, now: datetime) -> bool:
    """True if the row has been around past the hard cap."""
    age = now - _ensure_utc(delivery.created_at)
    return age.total_seconds() >= WEBHOOK_RETRY_MAX_AGE_SECONDS


async def _record_outcome(
    session: AsyncSession,
    delivery: WebhookDelivery,
    *,
    success: bool,
    status_code: int | None,
    error: str | None,
    now: datetime,
) -> None:
    """Update the row based on the attempt result."""
    delivery.attempt_count += 1
    delivery.last_attempt_at = now
    delivery.last_status_code = status_code
    delivery.last_error = error
    if delivery.first_attempted_at is None:
        delivery.first_attempted_at = now
    delivery.updated_at = now

    if success:
        delivery.status = WebhookDeliveryStatus.SUCCEEDED
        logger.info(
            "Webhook delivered task=%s url=%s attempts=%d status=%d",
            delivery.task_id,
            delivery.url,
            delivery.attempt_count,
            status_code or 0,
        )
    elif _too_old(delivery, now):
        delivery.status = WebhookDeliveryStatus.ABANDONED
        logger.error(
            "Webhook abandoned (>%ds old) task=%s url=%s attempts=%d last=%s",
            WEBHOOK_RETRY_MAX_AGE_SECONDS,
            delivery.task_id,
            delivery.url,
            delivery.attempt_count,
            error,
        )
    else:
        delay = backoff_delay(delivery.attempt_count)
        delivery.next_attempt_at = now + timedelta(seconds=delay)
        logger.warning(
            "Webhook attempt failed task=%s url=%s attempts=%d next_in=%ds error=%s",
            delivery.task_id,
            delivery.url,
            delivery.attempt_count,
            delay,
            error,
        )

    session.add(delivery)
    await session.commit()


async def process_due_deliveries(session: AsyncSession, *, batch_size: int = 50) -> int:
    """Drive one tick of the queue. Returns count of attempts made.

    Called repeatedly by `webhook_scheduler`. Safe to call from a
    test directly (no scheduler dependency)."""
    now = datetime.now(timezone.utc)

    deliveries = await _claim_due_deliveries(session, now=now, limit=batch_size)
    for delivery in deliveries:
        success, status_code, error = await _attempt_post(delivery)
        await _record_outcome(
            session,
            delivery,
            success=success,
            status_code=status_code,
            error=error,
            now=datetime.now(timezone.utc),
        )
    return len(deliveries)


async def redeliver(
    session: AsyncSession, delivery_id: str, *, now: datetime | None = None
) -> WebhookDelivery | None:
    """Reset a row to PENDING and due-now, regardless of its current
    status. Used by the admin redrive endpoint for the case where an
    operator brings the receiver back online days after an outage and
    wants the queued (or already-abandoned) deliveries re-attempted.

    Returns the updated row, or `None` if no row matched.
    """
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return None
    when = now or datetime.now(timezone.utc)
    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.next_attempt_at = when
    delivery.updated_at = when
    session.add(delivery)
    await session.commit()
    return delivery
