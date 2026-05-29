"""Public Block Kit surfaces: the review modal and the link-out message.

The dispatcher `_field_to_blocks` routes each primitive to its category-
specific element renderer and wraps the result in an `input` block. Non-
input primitives (DisplayText, Divider, Section, Image) render as
top-level blocks directly.

Shape of the returned modal view matches
https://api.slack.com/reference/surfaces/views.
"""

from __future__ import annotations

from typing import Any

from awaithumans.forms import FormDefinition
from awaithumans.forms.fields.date_time import (
    DatePicker,
    DateTimePicker,
    TimePicker,
)
from awaithumans.forms.fields.layout import Divider, Section
from awaithumans.forms.fields.media import FileUpload, Image
from awaithumans.forms.fields.numeric import OpinionScale, Slider, StarRating
from awaithumans.forms.fields.selection import (
    MultiSelect,
    PictureChoice,
    SingleSelect,
    Switch,
)
from awaithumans.forms.fields.text import DisplayText, LongText, ShortText
from awaithumans.server.channels.slack.blocks.date_time import (
    date_element,
    datetime_element,
    time_element,
)
from awaithumans.server.channels.slack.blocks.helpers import (
    UnrenderableInSlackError,
    input_block,
    truncate,
)
from awaithumans.server.channels.slack.blocks.media import file_upload_element
from awaithumans.server.channels.slack.blocks.numeric import (
    opinion_scale_element,
    slider_element,
    star_rating_element,
)
from awaithumans.server.channels.slack.blocks.selection import (
    multi_select_element,
    picture_choice_element,
    single_select_element,
    switch_element,
)
from awaithumans.server.channels.slack.blocks.text import (
    long_text_element,
    short_text_element,
)
from awaithumans.utils.constants import (
    SLACK_CONTEXT_VALUE_MAX,
    SLACK_HEADER_TEXT_MAX,
    SLACK_MODAL_CALLBACK_ID,
    SLACK_PLAIN_TEXT_MAX,
)


def form_to_modal(
    *,
    form: FormDefinition,
    task_id: str,
    task_title: str,
    task_payload: dict[str, Any] | None,
    redact_payload: bool = False,
    task_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a Block Kit modal view for a form.

    `task_id` is stored in `private_metadata` so the view_submission
    handler can look up the task without trusting the Slack-side state.

    `task_metadata` (when present) renders as a context block between
    the header and the payload preview so the reviewer sees free-form
    context (customer name, business vertical, etc.) before they start
    filling out the form.
    """
    blocks: list[dict[str, Any]] = []

    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": truncate(task_title, SLACK_HEADER_TEXT_MAX),
            },
        }
    )

    blocks.extend(task_metadata_blocks(task_metadata))

    if task_payload and not redact_payload:
        lines = [
            f"*{k}*: {truncate(str(v), SLACK_CONTEXT_VALUE_MAX)}" for k, v in task_payload.items()
        ]
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )
        blocks.append({"type": "divider"})

    for field in form.fields:
        blocks.extend(_field_to_blocks(field))

    return {
        "type": "modal",
        "callback_id": SLACK_MODAL_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Review task"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": task_id,
        "blocks": blocks,
    }


# Truncation for the task title in the link-out message. Slack renders
# sections as mrkdwn and handles long text, but we keep it short for
# notification surfaces where Slack truncates aggressively.
_MESSAGE_TITLE_MAX = 200

# Limits on how task_metadata renders in Slack. Slack's "context" block
# accepts up to 10 elements, each ~256 chars of text, but the visual
# weight gets noisy fast — five well-chosen key-value pairs leave room
# for the rest of the message. Operators wanting richer structure
# should put it on the payload, which already has channel-specific
# rendering.
_METADATA_MAX_ENTRIES = 5
_METADATA_VALUE_MAX = 80


def task_metadata_blocks(
    metadata: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Render task_metadata as a Slack `context` block + divider.

    Empty / None metadata returns an empty list so callers can splat
    the result unconditionally. Keys are bolded; values are
    truncated. We don't sort the dict here — preserving insertion
    order respects the caller's choice about which key matters most.
    """
    if not metadata:
        return []

    items = list(metadata.items())[:_METADATA_MAX_ENTRIES]
    overflow = len(metadata) - len(items)

    line = " · ".join(
        f"*{k}*: {truncate(str(v), _METADATA_VALUE_MAX)}" for k, v in items
    )
    if overflow > 0:
        line += f" · _+{overflow} more_"

    return [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": line}],
        }
    ]


def open_review_message_blocks(
    *,
    task_id: str,
    task_title: str,
    review_url: str,
    open_button_action_id: str,
    unsupported_fields: list[str] | None = None,
    broadcast: bool = False,
    claim_button_action_id: str | None = None,
    task_metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Initial message posted to a channel/user when a task is created.

    Always includes a "Review in dashboard" link-out. If the form is
    fully Slack-renderable, also includes an "Open in Slack" button
    that triggers the interactivity webhook to open a modal.

    When `broadcast=True` (notify targets a channel, not a DM), adds a
    "Claim this task" button as the primary call-to-action. The claim
    handler atomically assigns the task to the clicker and opens the
    modal for them. Later clickers get an ephemeral "already claimed"
    response — see routes/slack/interactions.py.
    """
    text = f"*New task to review:* {truncate(task_title, _MESSAGE_TITLE_MAX)}"
    if unsupported_fields:
        text += (
            f"\n_Contains {len(unsupported_fields)} field(s) that must "
            "be completed in the dashboard._"
        )

    elements: list[dict[str, Any]] = []
    if broadcast and claim_button_action_id:
        # Broadcast: claim is the primary action. We skip the separate
        # "Open in Slack" button — clicking claim both assigns AND
        # opens the modal in one step (modal flow is downstream of the
        # atomic claim).
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Claim this task"},
                "style": "primary",
                "action_id": claim_button_action_id,
                "value": task_id,
            }
        )
    elif not unsupported_fields:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open in Slack"},
                "style": "primary",
                "action_id": open_button_action_id,
                "value": task_id,
            }
        )
    elements.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Review in dashboard"},
            "url": review_url,
            "action_id": "awaithumans.open_dashboard",
        }
    )

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]
    blocks.extend(task_metadata_blocks(task_metadata))
    blocks.append({"type": "actions", "elements": elements})
    return blocks


def terminal_message_blocks(
    *,
    task_title: str,
    status: str,
    completed_by_display: str | None,
    review_url: str | None,
) -> list[dict[str, Any]]:
    """Replacement blocks posted via chat.update once the task is no
    longer actionable.

    Strips the action buttons so a recipient reading their DMs days
    later doesn't think there's still work to do. Keeps a "View in
    dashboard" link when we have one — the audit trail and the
    submitted response are useful even after the task is closed.

    `status` is one of "completed" / "cancelled" / "timed_out". The
    label and emoji are picked from the status; the dashboard's
    own status badge has the canonical visualization, this is a
    one-line summary.

    `completed_by_display` is the human-readable name we show
    after the status. None for `timed_out` (no human did it) or
    when the completer's identity isn't recorded.
    """
    label, emoji = _terminal_label_emoji(status)
    headline = f"{emoji} *{label}:* {truncate(task_title, _MESSAGE_TITLE_MAX)}"
    if completed_by_display and status != "timed_out":
        headline += f" — by {completed_by_display}"

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}}
    ]

    if review_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in dashboard"},
                        "url": review_url,
                        "action_id": "awaithumans.open_dashboard",
                    }
                ],
            }
        )

    return blocks


def _terminal_label_emoji(status: str) -> tuple[str, str]:
    """Map a terminal task status to a (label, emoji) pair.

    Verifier-rejected attempts come through as `verification_exhausted`
    once the agent gave up; we surface that as "Verifier rejected" so
    operators know the human's response wasn't accepted, not that the
    task was abandoned. Any unexpected status falls back to
    a neutral "Closed" — better than crashing the update path."""
    if status == "completed":
        return ("Completed", ":white_check_mark:")
    if status == "cancelled":
        return ("Cancelled", ":no_entry:")
    if status == "timed_out":
        return ("Timed out", ":hourglass:")
    if status == "verification_exhausted":
        return ("Verifier rejected", ":x:")
    return ("Closed", ":lock:")


def claimed_message_blocks(
    *,
    task_title: str,
    review_url: str,
    claimed_by_display: str,
) -> list[dict[str, Any]]:
    """Replacement blocks posted via chat.update after a successful
    claim. Shows who claimed the task and keeps a dashboard link for
    observers."""
    text = f"*Claimed by {claimed_by_display}:* {truncate(task_title, _MESSAGE_TITLE_MAX)}"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in dashboard"},
                    "url": review_url,
                    "action_id": "awaithumans.open_dashboard",
                }
            ],
        },
    ]


# ─── Dispatch ────────────────────────────────────────────────────────────


def _field_to_blocks(field: Any) -> list[dict[str, Any]]:
    """Render a single primitive as 1..N Block Kit blocks."""
    # Non-input primitives first — these render as top-level blocks.
    if isinstance(field, DisplayText):
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": truncate(field.text, SLACK_PLAIN_TEXT_MAX),
                },
            }
        ]
    if isinstance(field, Divider):
        return [{"type": "divider"}]
    if isinstance(field, Section):
        out: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": truncate(field.title, SLACK_HEADER_TEXT_MAX),
                },
            }
        ]
        if field.subtitle:
            out.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": field.subtitle}],
                }
            )
        return out
    if isinstance(field, Image):
        return [
            {
                "type": "image",
                "image_url": field.url,
                "alt_text": field.alt or field.label or "image",
            }
        ]

    # Input primitives — wrap each element in an `input` block.
    element_for_kind: dict[type, Any] = {
        Switch: switch_element,
        ShortText: short_text_element,
        LongText: long_text_element,
        SingleSelect: single_select_element,
        MultiSelect: multi_select_element,
        PictureChoice: picture_choice_element,
        DatePicker: date_element,
        DateTimePicker: datetime_element,
        TimePicker: time_element,
        Slider: slider_element,
        StarRating: star_rating_element,
        OpinionScale: opinion_scale_element,
        FileUpload: file_upload_element,
    }
    for kind, renderer in element_for_kind.items():
        if isinstance(field, kind):
            return [input_block(field, renderer(field))]

    raise UnrenderableInSlackError(
        f"Primitive '{field.kind}' has no native Slack renderer. "
        "Check form_renders_in(form, 'slack') before calling form_to_modal()."
    )
