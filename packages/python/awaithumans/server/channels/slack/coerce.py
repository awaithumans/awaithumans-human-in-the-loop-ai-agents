"""Slack view_submission → typed response dict.

Slack returns submitted values as a nested dict keyed by block_id → action_id
with per-element-type shapes (`selected_option`, `selected_options`, `value`,
`selected_date`, etc.). This module walks the form definition (the source of
truth for expected types) and pulls the right field from each action blob.

The result is a flat `{field_name: value}` dict matching the developer's
response Pydantic schema. Missing required fields appear as None; the
server's response-validation layer catches those.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from awaithumans.forms import FormDefinition
from awaithumans.forms.fields.date_time import (
    DatePicker,
    DateTimePicker,
    TimePicker,
)
from awaithumans.forms.fields.media import FileUpload
from awaithumans.forms.fields.numeric import OpinionScale, Slider, StarRating
from awaithumans.forms.fields.selection import (
    MultiSelect,
    PictureChoice,
    SingleSelect,
    Switch,
)
from awaithumans.forms.fields.text import LongText, ShortText
from awaithumans.utils.constants import SLACK_BLOCK_ID_PREFIX


def slack_values_to_response(
    form: FormDefinition,
    view_state: dict[str, Any],
) -> dict[str, Any]:
    """Convert a Slack view_submission `state` into a flat response dict."""
    values = view_state.get("values", {}) if view_state else {}
    response: dict[str, Any] = {}

    for field in form.fields:
        if not field.name:
            continue  # layout/display element — no response value

        block_id = f"{SLACK_BLOCK_ID_PREFIX}{field.name}"
        action = values.get(block_id, {}).get(field.name, {})
        response[field.name] = _extract_value(field, action)

    return response


def _extract_value(field: Any, action: dict[str, Any]) -> Any:
    """Dispatch per primitive. Returns None if the field was left blank."""
    if isinstance(field, Switch):
        opt = action.get("selected_option")
        return None if opt is None else opt.get("value") == "true"

    if isinstance(field, ShortText):
        value = action.get("value")
        if value in (None, ""):
            return None
        if field.subtype == "number":
            return _parse_number(value, decimal=False)
        if field.subtype == "currency":
            return _parse_number(value, decimal=True)
        return value

    if isinstance(field, LongText):
        return action.get("value") or None

    if isinstance(field, SingleSelect):
        opt = action.get("selected_option")
        return opt.get("value") if opt else None

    if isinstance(field, MultiSelect):
        return [o["value"] for o in action.get("selected_options", []) if "value" in o]

    if isinstance(field, PictureChoice):
        if field.multiple:
            return [o["value"] for o in action.get("selected_options", []) if "value" in o]
        opt = action.get("selected_option")
        return [opt["value"]] if opt and "value" in opt else []

    if isinstance(field, DatePicker):
        return action.get("selected_date")

    if isinstance(field, DateTimePicker):
        ts = action.get("selected_date_time")
        if ts is None:
            return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()

    if isinstance(field, TimePicker):
        return action.get("selected_time")

    if isinstance(field, Slider):
        value = action.get("value")
        return _parse_number(value, decimal=True) if value not in (None, "") else None

    if isinstance(field, StarRating):
        opt = action.get("selected_option")
        return int(opt["value"]) if opt and "value" in opt else None

    if isinstance(field, OpinionScale):
        opt = action.get("selected_option")
        return int(opt["value"]) if opt and "value" in opt else None

    if isinstance(field, FileUpload):
        files = action.get("files") or []
        return [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "mime": f.get("mimetype"),
                "url": f.get("url_private"),
                "size": f.get("size"),
            }
            for f in files
        ]

    # Any other kind means the form shouldn't have been rendered in Slack.
    return None


def _parse_number(raw: Any, *, decimal: bool) -> float | int | None:
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if decimal:
        return f
    return int(f)
