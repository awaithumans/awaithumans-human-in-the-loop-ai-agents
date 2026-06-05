"""Send demo-specific result emails (live-result receipt + 72h fallback).

The review-complete email is a RECEIPT: the visitor already saw the
reviewer's corrections live in their browser. The email captures the
artifact + drives signup. The fallback email is for when the reviewer
never claimed and the page timeout fired.

Both send paths follow the same shape as the task notifier
(``channels/email/notifier.py``): resolve the env-default transport,
build an ``EmailMessage`` against the visitor's address, send, and
update ``DemoRecord.status`` accordingly. Errors are logged and
swallowed — a failed send never raises out of the demo flow.

Templates use ``string.Template`` (``$variable``) to match the rest of
the email channel (see ``templates/renderers.py``); we deliberately
avoid pulling Jinja2 into the server install.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from functools import cache
from importlib import resources
from string import Template
from typing import Any

from awaithumans.server.channels.email.transport.base import (
    EmailMessage,
    EmailTransport,
    EmailTransportError,
)
from awaithumans.server.channels.email.transport.factory import (
    resolve_default_transport,
)
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_async_session_factory
from awaithumans.server.db.models import DemoRecord, DemoStatus

logger = logging.getLogger("awaithumans.server.channels.email.demo_email")


@cache
def _load(name: str) -> Template:
    """Load a demo template file once and cache it."""
    text = (
        resources.files("awaithumans.server.channels.email.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return Template(text)


def _signup_url() -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}/setup?utm_source=demo&utm_medium=email"


def _demo_url() -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}/awaitverify/demo"


def _pretty(value: dict[str, Any] | None) -> str:
    """Pretty JSON for plain-text contexts (.txt templates)."""
    return json.dumps(value or {}, indent=2)


def _pretty_html(value: dict[str, Any] | None) -> str:
    """Pretty JSON, HTML-escaped for safe embedding in HTML templates.

    `record.ai_result` and `record.field_corrections` carry values
    originating from the AI extractor and from reviewer submissions.
    Both can contain attacker-controllable strings, so HTML metacharacters
    must be escaped before they land in the email body.
    """
    return html.escape(json.dumps(value or {}, indent=2), quote=False)


def _safe_url(value: str) -> str:
    """Escape operator-configured URLs for safe use inside an href attribute."""
    return html.escape(value, quote=True)


def _final_result(record: DemoRecord) -> dict[str, Any]:
    """Merge AI result with reviewer corrections.

    The reviewer's per-field corrections win over the AI's initial
    extraction — same merge order the visitor saw on screen during
    the live result phase.
    """
    merged: dict[str, Any] = dict(record.ai_result or {})
    merged.update(record.field_corrections or {})
    return merged


def _build_message(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    demo_id: str,
) -> EmailMessage | None:
    """Assemble the EmailMessage or return None if no From: is set.

    The demo flow only honors the env-configured default identity
    (``AWAITHUMANS_EMAIL_FROM`` etc.) — there's no per-route identity
    on a demo email, so we don't go through the dashboard identities
    table. If no From: is configured, we log-and-skip the same way
    the task notifier does.
    """
    if not settings.EMAIL_FROM:
        logger.warning(
            "Demo email skipped (record %s): no AWAITHUMANS_EMAIL_FROM set.",
            demo_id,
        )
        return None
    return EmailMessage(
        to=to,
        subject=subject,
        html=html,
        text=text,
        from_email=settings.EMAIL_FROM,
        from_name=settings.EMAIL_FROM_NAME,
        reply_to=settings.EMAIL_REPLY_TO,
        tags={"demo_id": demo_id, "kind": "demo"},
    )


async def send_demo_review_complete_email(record: DemoRecord) -> None:
    """Send the receipt email after a reviewer finished the live demo."""
    transport: EmailTransport | None = resolve_default_transport()
    if transport is None:
        logger.warning(
            "No email transport configured; skipping demo receipt for %s.",
            record.id,
        )
        return

    html_ctx = {
        "ai_result_pretty": _pretty_html(record.ai_result),
        "corrections_pretty": _pretty_html(record.field_corrections),
        "final_pretty": _pretty_html(_final_result(record)),
        "signup_url": _safe_url(_signup_url()),
        "booking_url": _safe_url(settings.DEMO_BOOKING_URL),
        "demo_url": _safe_url(_demo_url()),
    }
    text_ctx = {
        "ai_result_pretty": _pretty(record.ai_result),
        "corrections_pretty": _pretty(record.field_corrections),
        "final_pretty": _pretty(_final_result(record)),
        "signup_url": _signup_url(),
        "booking_url": settings.DEMO_BOOKING_URL,
        "demo_url": _demo_url(),
    }
    html_body = _load("demo_review_complete.html").substitute(**html_ctx)
    text = _load("demo_review_complete.txt").substitute(**text_ctx)

    message = _build_message(
        to=record.email,
        subject="Your AwaitVerify demo receipt",
        html=html_body,
        text=text,
        demo_id=record.id,
    )
    if message is None:
        return

    try:
        result = await transport.send(message)
    except EmailTransportError as exc:
        logger.error("Demo receipt email send failed for %s: %s", record.id, exc)
        await _mark_status(record.id, DemoStatus.EMAIL_FAILED)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected demo receipt email error for %s: %s",
            record.id,
            exc,
        )
        await _mark_status(record.id, DemoStatus.EMAIL_FAILED)
        return

    logger.info(
        "Demo receipt email sent for %s (transport=%s, id=%s).",
        record.id,
        result.transport,
        result.message_id,
    )
    await _mark_status(record.id, DemoStatus.EMAIL_SENT, stamp_sent=True)


async def send_demo_fallback_email(record: DemoRecord) -> None:
    """Send the AI-only fallback when no reviewer ever claimed the task."""
    transport: EmailTransport | None = resolve_default_transport()
    if transport is None:
        logger.warning(
            "No email transport configured; skipping demo fallback for %s.",
            record.id,
        )
        return

    html_ctx = {
        "ai_result_pretty": _pretty_html(record.ai_result),
        "signup_url": _safe_url(_signup_url()),
    }
    text_ctx = {
        "ai_result_pretty": _pretty(record.ai_result),
        "signup_url": _signup_url(),
    }
    html_body = _load("demo_fallback.html").substitute(**html_ctx)
    text = _load("demo_fallback.txt").substitute(**text_ctx)

    message = _build_message(
        to=record.email,
        subject="Your AwaitVerify demo result",
        html=html_body,
        text=text,
        demo_id=record.id,
    )
    if message is None:
        return

    try:
        result = await transport.send(message)
    except EmailTransportError as exc:
        logger.error("Demo fallback email send failed for %s: %s", record.id, exc)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected demo fallback email error for %s: %s",
            record.id,
            exc,
        )
        return

    logger.info(
        "Demo fallback email sent for %s (transport=%s, id=%s).",
        record.id,
        result.transport,
        result.message_id,
    )
    await _mark_status(record.id, DemoStatus.FALLBACK_SENT, stamp_sent=True)


async def _mark_status(
    demo_id: str,
    new_status: DemoStatus,
    *,
    stamp_sent: bool = False,
) -> None:
    """Update the DemoRecord status (and email_sent_at) post-send."""
    factory = get_async_session_factory()
    async with factory() as session:
        fresh = await session.get(DemoRecord, demo_id)
        if fresh is None:
            logger.warning(
                "Demo %s vanished before status update to %s.",
                demo_id,
                new_status.value,
            )
            return
        fresh.status = new_status
        if stamp_sent:
            fresh.email_sent_at = datetime.now(timezone.utc)
        await session.commit()


__all__ = [
    "send_demo_fallback_email",
    "send_demo_review_complete_email",
]
