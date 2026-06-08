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
    extraction. Same merge order the visitor saw on screen during
    the live result phase.
    """
    merged: dict[str, Any] = dict(record.ai_result or {})
    merged.update(record.field_corrections or {})
    return merged


def _count_top_level_diffs(
    ai_result: dict[str, Any] | None,
    field_corrections: dict[str, Any] | None,
) -> int:
    """Count top-level keys where the reviewer's correction differs from AI.

    Uses deep equality (Python ``==`` already does this for dict/list of
    primitives). Only counts keys present in ``field_corrections`` since
    those are the fields the reviewer touched.
    """
    ai = ai_result or {}
    corrections = field_corrections or {}
    count = 0
    for key, corrected_value in corrections.items():
        if ai.get(key) != corrected_value:
            count += 1
    return count


def _fmt_primitive(value: Any) -> str:
    """Format a primitive (or None) for HTML display, HTML-escaped."""
    if value is None or value == "":
        return html.escape("(empty)", quote=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return html.escape(str(value), quote=False)
    # Fallback for anything unexpected.
    return html.escape(json.dumps(value), quote=False)


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _summary_label(value: Any) -> str:
    """One-line summary used inside <summary> for a complex value."""
    if isinstance(value, list):
        n = len(value)
        return f"[{n} item{'s' if n != 1 else ''}]"
    if isinstance(value, dict):
        n = len(value)
        return f"{{{n} field{'s' if n != 1 else ''}}}"
    return ""


# Inline CSS constants used by the tree renderer. Email clients still don't
# render external stylesheets reliably, so we inline everything.
_ROW_STYLE = (
    "margin:2px 0;padding:2px 0;font-family:ui-monospace,SFMono-Regular,"
    "Menlo,Monaco,Consolas,monospace;font-size:13px;line-height:1.55;"
)
_KEY_STYLE = "color:#555;margin-right:6px;"
_VALUE_STYLE = "color:#0A0A0A;"
_AI_OLD_STYLE = "color:#ef4444;text-decoration:line-through;margin-right:8px;"
_NEW_STYLE = "color:#22c55e;font-weight:600;"
_SUMMARY_STYLE = (
    "cursor:pointer;color:#555;font-family:ui-monospace,SFMono-Regular,"
    "Menlo,Monaco,Consolas,monospace;font-size:13px;padding:2px 0;"
)
_DETAILS_STYLE = (
    "margin:2px 0 2px 0;padding-left:12px;border-left:1px solid #ddd;"
)


def _render_tree_html(
    value: Any,
    prior: Any,
    depth: int,
    list_index: int | None,
    label: str | None,
) -> str:
    """Render a value (with optional AI prior) as a nested <details> tree.

    Default-open rules:
    - Records: open for the first two depth levels (depth < 2).
    - Lists: only [0] is open by default. Index 1+ stays closed.

    Note: <details>/<summary> is supported by Apple Mail, Gmail web,
    Spark, and most modern clients. Outlook desktop ignores the tag and
    falls back to showing everything as flat text. That is acceptable
    for v1.
    """
    # Primitive leaf: render row with optional diff (AI value on the left
    # struck-through, reviewer value on the right).
    if _is_primitive(value) and _is_primitive(prior):
        if prior is not None and prior != value and prior != "":
            return (
                f'<div style="{_ROW_STYLE}">'
                f'<span style="{_KEY_STYLE}">'
                f"{html.escape(label or '', quote=False)}{':' if label else ''}"
                f"</span>"
                f'<span style="{_AI_OLD_STYLE}">{_fmt_primitive(prior)}</span>'
                f'<span style="{_NEW_STYLE}">{_fmt_primitive(value)}</span>'
                f"</div>"
            )
        return (
            f'<div style="{_ROW_STYLE}">'
            f'<span style="{_KEY_STYLE}">'
            f"{html.escape(label or '', quote=False)}{':' if label else ''}"
            f"</span>"
            f'<span style="{_VALUE_STYLE}">{_fmt_primitive(value)}</span>'
            f"</div>"
        )

    # Complex (list or dict). Render as <details>.
    if isinstance(value, list):
        # Lists: index 0 open, rest closed.
        is_open = list_index is None or list_index == 0
        summary_text = (
            f"{html.escape(label, quote=False)} {_summary_label(value)}"
            if label
            else f"[{list_index}] {_summary_label(value)}"
            if list_index is not None
            else _summary_label(value)
        )
        # When label is None and list_index is None we are at the root.
        open_attr = " open" if is_open else ""
        children_html: list[str] = []
        prior_list = prior if isinstance(prior, list) else []
        for i, item in enumerate(value):
            prior_item = prior_list[i] if i < len(prior_list) else None
            children_html.append(
                _render_tree_html(item, prior_item, depth + 1, i, None)
            )
        return (
            f'<details{open_attr} style="{_DETAILS_STYLE}">'
            f'<summary style="{_SUMMARY_STYLE}">{summary_text}</summary>'
            f"{''.join(children_html)}"
            "</details>"
        )

    if isinstance(value, dict):
        # Records: open while depth < 2.
        is_open = depth < 2
        summary_text = (
            f"{html.escape(label, quote=False)} {_summary_label(value)}"
            if label
            else f"[{list_index}]"
            if list_index is not None
            else _summary_label(value)
        )
        open_attr = " open" if is_open else ""
        children_html = []
        prior_dict = prior if isinstance(prior, dict) else {}
        for key, child in value.items():
            child_prior = prior_dict.get(key)
            children_html.append(
                _render_tree_html(child, child_prior, depth + 1, None, key)
            )
        return (
            f'<details{open_attr} style="{_DETAILS_STYLE}">'
            f'<summary style="{_SUMMARY_STYLE}">{summary_text}</summary>'
            f"{''.join(children_html)}"
            "</details>"
        )

    # Mixed primitive/complex (e.g. value is dict but prior was a string).
    # Render the new value as a tree without diffing against the unrelated
    # prior, and surface the prior as a struck-through note above.
    body = _render_tree_html(value, None, depth, list_index, label)
    if prior is not None and prior != "" and _is_primitive(prior):
        note = (
            f'<div style="{_ROW_STYLE}">'
            f'<span style="{_AI_OLD_STYLE}">{_fmt_primitive(prior)}</span>'
            "</div>"
        )
        return note + body
    return body


def _render_diff_tree(
    ai_result: dict[str, Any] | None,
    final_result: dict[str, Any],
) -> str:
    """Render the top-level diff tree HTML string.

    The top-level container is a div (no <details> wrap) so the user
    always sees the full record without needing to click. Nested records
    follow the depth < 2 rule; nested lists follow the index == 0 rule.
    """
    parts: list[str] = []
    ai = ai_result or {}
    for key, value in final_result.items():
        prior = ai.get(key)
        parts.append(_render_tree_html(value, prior, 0, None, key))
    return "".join(parts)


def _render_text_summary(
    ai_result: dict[str, Any] | None,
    final_result: dict[str, Any],
) -> str:
    """Plain-text fallback for the receipt email.

    Flat ``key: value`` listing. For changed fields, append the prior AI
    value with a ``(was: X)`` trailer so reviewers using a text-only
    client still see the diff.
    """
    ai = ai_result or {}
    lines: list[str] = []
    for key, value in final_result.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, indent=2)
            prior = ai.get(key)
            if prior is not None and prior != value:
                lines.append(
                    f"{key}: {rendered}\n  (was: {json.dumps(prior)})"
                )
            else:
                lines.append(f"{key}: {rendered}")
            continue
        prior = ai.get(key)
        if prior is not None and prior != value and prior != "":
            lines.append(f"{key}: {value}  (was: {prior})")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


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

    final = _final_result(record)
    correction_count = _count_top_level_diffs(
        record.ai_result, record.field_corrections
    )
    diff_tree_html = _render_diff_tree(record.ai_result, final)
    text_summary = _render_text_summary(record.ai_result, final)

    html_ctx = {
        "correction_count": str(correction_count),
        "correction_word": "correction" if correction_count == 1 else "corrections",
        "diff_tree_html": diff_tree_html,
        "signup_url": _safe_url(_signup_url()),
        "booking_url": _safe_url(settings.DEMO_BOOKING_URL),
        "demo_url": _safe_url(_demo_url()),
    }
    text_ctx = {
        "correction_count": str(correction_count),
        "correction_word": "correction" if correction_count == 1 else "corrections",
        "result_summary": text_summary,
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
