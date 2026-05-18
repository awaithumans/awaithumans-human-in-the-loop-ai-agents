"""Render a task's notification email.

Decides per-form:

- If the form contains EXACTLY one primitive that's a `switch` or a
  small `single_select` (≤4 options) and no other input fields,
  we emit magic-link buttons for each value.
- Otherwise, we emit just a "Review in dashboard" link-out. Complex
  forms (file_upload, table, subform, anything with multiple inputs)
  should be completed in the dashboard.

The output is an EmailMessage ready for a transport.
"""

from __future__ import annotations

from typing import Any

from awaithumans.forms import FormDefinition
from awaithumans.forms.fields.selection import SingleSelect, Switch
from awaithumans.server.channels.email.magic_links import sign_action_token
from awaithumans.server.channels.email.templates import (
    ButtonSpec,
    notification_html,
    notification_text,
)
from awaithumans.server.channels.email.transport.base import EmailMessage


def _magic_link_url(public_url: str, token: str) -> str:
    return f"{public_url.rstrip('/')}/api/channels/email/action/{token}"


def _review_url(public_url: str, task_id: str) -> str:
    """Plain dashboard URL — used when no recipient handoff is available
    (broadcast routes, email channel without a known recipient, etc.).

    The dashboard's task detail page is `/task?id=<id>` — the
    `/tasks/<id>` legacy form was removed when the dashboard switched
    to a static-export build (dynamic segments don't survive
    `output: "export"`). Slack's review URL was updated earlier; this
    line is the email-side fix.
    """
    return f"{public_url.rstrip('/')}/task?id={task_id}"


def _review_url_for_recipient(
    *,
    public_url: str,
    task_id: str,
    recipient: str,
    handoff_exp_unix: int | None,
) -> str:
    """Dashboard URL signed for the recipient when an expiry is given,
    plain URL otherwise.

    The signed URL hits `/api/auth/email-handoff?to=...&t=...&e=...&s=...`
    which mints a session for that email and redirects to /task. Lets
    a recipient with no dashboard password through the login wall —
    same DX the Slack channel has.
    """
    if handoff_exp_unix is None or not recipient:
        return _review_url(public_url, task_id)

    # Lazy import to avoid pulling the crypto path into modules that
    # only need the renderer for tests / docs preview.
    import urllib.parse

    from awaithumans.server.core.email_handoff import sign_handoff

    sig = sign_handoff(recipient=recipient, task_id=task_id, exp_unix=handoff_exp_unix)
    qs = urllib.parse.urlencode({"to": recipient, "t": task_id, "e": handoff_exp_unix, "s": sig})
    return f"{public_url.rstrip('/')}/api/auth/email-handoff?{qs}"


def _find_single_input_primitive(form: FormDefinition) -> Any | None:
    """Return the lone named input field iff that's all the form has.

    Layout fields (display_text, section, divider, image, etc.) are
    ignored. If there's exactly one named primitive, return it.
    Otherwise return None.
    """
    inputs = [f for f in form.fields if getattr(f, "name", "")]
    if len(inputs) != 1:
        return None
    return inputs[0]


def _buttons_for_form(
    form: FormDefinition | None,
    *,
    task_id: str,
    recipient: str,
    public_url: str,
) -> list[ButtonSpec]:
    """Build magic-link buttons, or empty list for link-out-only forms.

    `recipient` is baked into each token so the action route can
    stamp `completed_by_email` on the task. Without it, the audit
    log shows "—" for every email completion.
    """
    if form is None:
        return []

    field = _find_single_input_primitive(form)
    if field is None:
        return []

    if isinstance(field, Switch):
        approve_token = sign_action_token(
            task_id=task_id,
            field_name=field.name,
            value=True,
            recipient=recipient,
        )
        reject_token = sign_action_token(
            task_id=task_id,
            field_name=field.name,
            value=False,
            recipient=recipient,
        )
        return [
            ButtonSpec(
                label=field.true_label,
                url=_magic_link_url(public_url, approve_token),
                style="primary",
            ),
            ButtonSpec(
                label=field.false_label,
                url=_magic_link_url(public_url, reject_token),
                style="danger",
            ),
        ]

    if isinstance(field, SingleSelect) and len(field.options) <= 4:
        return [
            ButtonSpec(
                label=opt.label,
                url=_magic_link_url(
                    public_url,
                    sign_action_token(
                        task_id=task_id,
                        field_name=field.name,
                        value=opt.value,
                        recipient=recipient,
                    ),
                ),
                style="primary" if i == 0 else "neutral",
            )
            for i, opt in enumerate(field.options)
        ]

    return []


def _payload_lines(payload: dict[str, Any] | None, redacted: bool) -> list[tuple[str, str]]:
    if not payload or redacted:
        return []
    lines: list[tuple[str, str]] = []
    for key, value in payload.items():
        text = str(value)
        if len(text) > 300:
            text = text[:297] + "…"
        lines.append((str(key), text))
    return lines


def build_notification_email(
    *,
    to: str,
    task_id: str,
    task_title: str,
    task_payload: dict[str, Any] | None,
    redact_payload: bool,
    form: FormDefinition | None,
    from_email: str,
    from_name: str | None,
    reply_to: str | None,
    public_url: str,
    handoff_exp_unix: int | None = None,
) -> EmailMessage:
    """Assemble the EmailMessage for one recipient.

    `handoff_exp_unix` (when provided) signs the "Review in dashboard"
    URL so the recipient lands authenticated even if they don't have
    a dashboard password yet — the endpoint auto-provisions a
    reviewer on first click. Pass `task.timeout_at` so the link
    expires when the task does. None falls back to the unsigned
    URL (existing behavior — recipients with no session bounce to
    /login).
    """
    review_url = _review_url_for_recipient(
        public_url=public_url,
        task_id=task_id,
        recipient=to,
        handoff_exp_unix=handoff_exp_unix,
    )
    buttons = _buttons_for_form(form, task_id=task_id, recipient=to, public_url=public_url)
    lines = _payload_lines(task_payload, redact_payload)

    html = notification_html(
        task_title=task_title,
        payload_lines=lines,
        redacted=redact_payload,
        buttons=buttons,
        review_url=review_url,
    )
    text = notification_text(
        task_title=task_title,
        payload_lines=lines,
        redacted=redact_payload,
        buttons=buttons,
        review_url=review_url,
    )

    subject = f"Review: {task_title}"

    return EmailMessage(
        to=to,
        subject=subject,
        html=html,
        text=text,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to,
        tags={"task_id": task_id},
    )
