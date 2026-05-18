"""Outbound webhook delivery — sign / verify, enqueue, retry queue.

Pins the things the durable adapters (Temporal, LangGraph) ride on:

  - The signature is HMAC-SHA256 over the EXACT body bytes the
    receiver gets, formatted as `sha256=<hex>`. Receivers can
    constant-time-verify with both `sha256=` prefix and bare hex.

  - `enqueue_completion_webhook` is a no-op when callback_url is
    missing (the dominant case — most tasks long-poll). When it IS
    set, a single PENDING row appears with the right URL, signature,
    and payload bytes.

  - `process_due_deliveries` is the queue's heart: claims due rows,
    POSTs them, advances state. Success → SUCCEEDED. Failure →
    PENDING with backoff. Aged-out failure → ABANDONED.

  - `backoff_delay` follows the configured schedule; values aren't
    hardcoded in the dispatcher.

  - `redeliver` resets a row regardless of current status, used by
    the admin redrive endpoint.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncGenerator, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    ConsumedEmailToken,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    TaskStatus,
    User,
    WebhookDelivery,
    WebhookDeliveryStatus,
)
from awaithumans.server.services.task_service import complete_task, create_task
from awaithumans.server.services.webhook_dispatch import (
    backoff_delay,
    enqueue_completion_webhook,
    process_due_deliveries,
    redeliver,
    sign_body,
    verify_signature,
)
from awaithumans.utils.constants import (
    WEBHOOK_RETRY_BACKOFF_SECONDS,
    WEBHOOK_RETRY_MAX_AGE_SECONDS,
)


@pytest.fixture(autouse=True)
def _payload_key() -> Iterator[None]:
    """HKDF derives the webhook key from PAYLOAD_KEY — required.

    Swaps both the pydantic-settings copy and the `os.environ` value
    because `awaithumans.utils.webhook_signing` reads directly from
    `os.environ` (PR #71)."""
    import os

    from awaithumans.utils import webhook_signing

    fresh = secrets.token_urlsafe(32)
    original_settings = settings.PAYLOAD_KEY
    original_env = os.environ.get("AWAITHUMANS_PAYLOAD_KEY")

    settings.PAYLOAD_KEY = fresh
    os.environ["AWAITHUMANS_PAYLOAD_KEY"] = fresh
    encryption.reset_key_cache()
    webhook_signing.reset_cache()
    yield
    settings.PAYLOAD_KEY = original_settings
    if original_env is None:
        os.environ.pop("AWAITHUMANS_PAYLOAD_KEY", None)
    else:
        os.environ["AWAITHUMANS_PAYLOAD_KEY"] = original_env
    encryption.reset_key_cache()
    webhook_signing.reset_cache()


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Force every AsyncClient inside webhook_dispatch through the
    given mock transport. The dispatcher passes `timeout=`, never
    `transport=`, so we strip any caller-supplied transport before
    threading ours in."""
    real_client = httpx.AsyncClient

    def _factory(**kw: Any) -> httpx.AsyncClient:
        kw.pop("transport", None)
        return real_client(transport=transport, **kw)

    monkeypatch.setattr(
        "awaithumans.server.services.webhook_dispatch.httpx.AsyncClient",
        _factory,
    )


# ─── sign / verify ────────────────────────────────────────────────────


def test_sign_body_returns_sha256_prefixed_hex() -> None:
    sig = sign_body(b'{"task_id":"abc"}')
    assert sig.startswith("sha256=")
    # HMAC-SHA256 → 64 hex chars
    assert len(sig) == len("sha256=") + 64


def test_verify_signature_accepts_correct_signature() -> None:
    body = b'{"hello":"world"}'
    sig = sign_body(body)
    assert verify_signature(body=body, signature=sig) is True


def test_verify_signature_accepts_bare_hex_without_prefix() -> None:
    """Some routing layers strip header prefixes; tolerate that."""
    body = b'{"hello":"world"}'
    full = sign_body(body)
    bare = full.removeprefix("sha256=")
    assert verify_signature(body=body, signature=bare) is True


def test_verify_signature_rejects_wrong_signature() -> None:
    body = b'{"hello":"world"}'
    assert verify_signature(body=body, signature="sha256=" + "0" * 64) is False


def test_verify_signature_rejects_missing_header() -> None:
    assert verify_signature(body=b"x", signature=None) is False
    assert verify_signature(body=b"x", signature="") is False


def test_verify_signature_body_change_invalidates() -> None:
    """Catches body-tamper. The HMAC is over the EXACT bytes the
    receiver gets — any modification flips the signature."""
    sig = sign_body(b'{"task_id":"abc"}')
    assert verify_signature(body=b'{"task_id":"abd"}', signature=sig) is False


# ─── backoff schedule ─────────────────────────────────────────────────


def test_backoff_delay_progresses_through_schedule() -> None:
    """Index 0 (first failure) maps to the first delay; subsequent
    failures step through the tuple."""
    assert backoff_delay(1) == WEBHOOK_RETRY_BACKOFF_SECONDS[0]
    assert backoff_delay(2) == WEBHOOK_RETRY_BACKOFF_SECONDS[1]
    assert backoff_delay(3) == WEBHOOK_RETRY_BACKOFF_SECONDS[2]


def test_backoff_delay_clamps_to_last_entry() -> None:
    """Past the schedule, stay at the largest delay — the age cap is
    what stops retries, not the schedule running out."""
    huge = len(WEBHOOK_RETRY_BACKOFF_SECONDS) + 5
    assert backoff_delay(huge) == WEBHOOK_RETRY_BACKOFF_SECONDS[-1]


# ─── enqueue ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_is_noop_when_callback_url_missing(
    session: AsyncSession,
) -> None:
    """The dominant case — most tasks long-poll, no row should be
    written. The dispatcher can be invoked unconditionally on every
    terminal transition without polluting the queue."""
    task, _ = await create_task(
        session,
        task="x",
        payload={},
        payload_schema={},
        response_schema={},
        timeout_seconds=900,
        idempotency_key="no-cb",
    )
    await enqueue_completion_webhook(session, task)
    rows = (await session.execute(select(WebhookDelivery))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_enqueue_creates_pending_row_with_signed_body(
    session: AsyncSession,
) -> None:
    """The row has the exact bytes a receiver would later HMAC-verify
    against. Storing the body up-front means retries re-send the
    same payload — the signature stays valid through transient
    failures."""
    task, _ = await create_task(
        session,
        task="Approve refund",
        payload={"amount": 100},
        payload_schema={},
        response_schema={},
        timeout_seconds=900,
        idempotency_key="webhook-test",
        callback_url="https://example.test/cb",
    )
    completed = await complete_task(session, task_id=task.id, response={"approved": True})

    await enqueue_completion_webhook(session, completed)

    row = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.task_id == completed.id)
        )
    ).scalar_one()

    assert row.status == WebhookDeliveryStatus.PENDING
    assert row.url == "https://example.test/cb"
    assert row.attempt_count == 0

    # Receiver round-trip: verify the persisted body against the
    # persisted signature.
    assert verify_signature(body=row.body, signature=row.signature) is True
    payload = json.loads(row.body)
    assert payload["task_id"] == completed.id
    assert payload["status"] == "completed"
    assert payload["response"] == {"approved": True}


# ─── process_due_deliveries ───────────────────────────────────────────


async def _enqueued_row(session: AsyncSession, *, callback_url: str) -> WebhookDelivery:
    task, _ = await create_task(
        session,
        task="t",
        payload={},
        payload_schema={},
        response_schema={},
        timeout_seconds=900,
        idempotency_key=f"k-{secrets.token_hex(4)}",
        callback_url=callback_url,
    )
    completed = await complete_task(session, task_id=task.id, response={"ok": True})
    await enqueue_completion_webhook(session, completed)
    row = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.task_id == completed.id)
        )
    ).scalar_one()
    return row


@pytest.mark.asyncio
async def test_process_marks_succeeded_on_2xx(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: receiver returns 200 → row transitions to
    SUCCEEDED with the status code captured for the audit trail."""
    received: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        received["body"] = request.content
        received["sig"] = request.headers.get("x-awaithumans-signature")
        return httpx.Response(200, json={"ok": True})

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    row = await _enqueued_row(session, callback_url="https://ok.test/cb")
    n = await process_due_deliveries(session)
    assert n == 1

    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.SUCCEEDED
    assert refreshed.last_status_code == 200
    assert refreshed.attempt_count == 1
    # The body that landed at the receiver is exactly what we stored.
    assert received["body"] == row.body
    assert verify_signature(body=received["body"], signature=received["sig"]) is True


@pytest.mark.asyncio
async def test_process_keeps_pending_with_backoff_on_network_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receiver refuses → row stays PENDING, attempt_count++ and
    next_attempt_at moves into the future by the configured first
    delay. The error message is stored for the dashboard."""

    async def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _patch_httpx(monkeypatch, httpx.MockTransport(boom))

    row = await _enqueued_row(session, callback_url="https://offline.test/cb")
    before = datetime.now(timezone.utc)
    await process_due_deliveries(session)

    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.PENDING
    assert refreshed.attempt_count == 1
    assert refreshed.last_error is not None and "ConnectError" in refreshed.last_error
    assert refreshed.next_attempt_at >= before + timedelta(
        seconds=WEBHOOK_RETRY_BACKOFF_SECONDS[0] - 1
    )


@pytest.mark.asyncio
async def test_process_keeps_pending_with_backoff_on_5xx(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receiver returns 503 → same retry shape as a network error.
    Status code is captured even though the attempt is treated as a
    failure."""

    async def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"err": "overloaded"})

    _patch_httpx(monkeypatch, httpx.MockTransport(fail))

    row = await _enqueued_row(session, callback_url="https://flaky.test/cb")
    await process_due_deliveries(session)

    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.PENDING
    assert refreshed.attempt_count == 1
    assert refreshed.last_status_code == 503


@pytest.mark.asyncio
async def test_process_marks_abandoned_past_age_cap(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row past the hard cap should NEVER be retried again, no
    matter how many ticks the scheduler does. We force the cap by
    backdating `created_at`."""

    async def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still down")

    _patch_httpx(monkeypatch, httpx.MockTransport(boom))

    row = await _enqueued_row(session, callback_url="https://gone.test/cb")
    # Backdate so the dispatcher's age check trips on this attempt.
    row.created_at = datetime.now(timezone.utc) - timedelta(
        seconds=WEBHOOK_RETRY_MAX_AGE_SECONDS + 1
    )
    session.add(row)
    await session.commit()

    await process_due_deliveries(session)

    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.ABANDONED


@pytest.mark.asyncio
async def test_process_skips_rows_not_yet_due(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future-dated row stays untouched; the claim query filters
    on `next_attempt_at <= now`."""
    seen = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        return httpx.Response(200)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    row = await _enqueued_row(session, callback_url="https://later.test/cb")
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=1)
    session.add(row)
    await session.commit()

    await process_due_deliveries(session)
    assert seen["n"] == 0
    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.PENDING
    assert refreshed.attempt_count == 0


@pytest.mark.asyncio
async def test_process_eventually_succeeds_after_failures(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two failures then a 200 — the row should land on SUCCEEDED
    after three ticks. We fast-forward `next_attempt_at` between
    ticks instead of sleeping through the real backoff."""
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    row = await _enqueued_row(session, callback_url="https://retried.test/cb")

    for _ in range(3):
        await process_due_deliveries(session)
        refreshed = await session.get(WebhookDelivery, row.id)
        assert refreshed is not None
        if refreshed.status == WebhookDeliveryStatus.SUCCEEDED:
            break
        # Fast-forward so the next process_due_deliveries call picks
        # the row up immediately instead of waiting on backoff.
        refreshed.next_attempt_at = datetime.now(timezone.utc)
        session.add(refreshed)
        await session.commit()

    refreshed = await session.get(WebhookDelivery, row.id)
    assert refreshed is not None
    assert refreshed.status == WebhookDeliveryStatus.SUCCEEDED
    assert counter["n"] == 3


# ─── redeliver ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redeliver_resets_abandoned_row_to_pending(
    session: AsyncSession,
) -> None:
    """The admin redrive path: an ABANDONED row should be eligible
    for delivery on the next scheduler tick after redeliver()."""
    row = await _enqueued_row(session, callback_url="https://x.test/cb")
    row.status = WebhookDeliveryStatus.ABANDONED
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(days=30)
    session.add(row)
    await session.commit()

    updated = await redeliver(session, row.id)
    assert updated is not None
    assert updated.status == WebhookDeliveryStatus.PENDING
    assert updated.next_attempt_at <= datetime.now(timezone.utc) + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_redeliver_returns_none_for_unknown_id(
    session: AsyncSession,
) -> None:
    assert await redeliver(session, "nope-not-real") is None
