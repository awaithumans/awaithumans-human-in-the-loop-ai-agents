"""Numeric element renderers: Slider, StarRating, OpinionScale.

Slack has no native slider or star-rating — both fall back to typed
Block Kit elements (`number_input` or `static_select`) that carry the
same bounds/semantics.
"""

from __future__ import annotations

from typing import Any

from awaithumans.forms.fields.numeric import OpinionScale, Slider, StarRating
from awaithumans.server.channels.slack.blocks.helpers import option


def slider_element(field: Slider) -> dict[str, Any]:
    elem: dict[str, Any] = {
        "type": "number_input",
        "action_id": field.name,
        "is_decimal_allowed": field.step < 1,
        "min_value": str(field.min),
        "max_value": str(field.max),
    }
    if field.default is not None:
        elem["initial_value"] = str(field.default)
    return elem


def star_rating_element(field: StarRating) -> dict[str, Any]:
    options = [option(str(v), "★" * v + "☆" * (field.max - v)) for v in range(1, field.max + 1)]
    elem: dict[str, Any] = {
        "type": "static_select",
        "action_id": field.name,
        "options": options,
    }
    if field.default:
        match = next((o for o in options if o["value"] == str(field.default)), None)
        if match:
            elem["initial_option"] = match
    return elem


def opinion_scale_element(field: OpinionScale) -> dict[str, Any]:
    values = list(range(field.min, field.max + 1))
    labels_suffix = ""
    if field.min_label and field.max_label:
        labels_suffix = f" ({field.min_label} → {field.max_label})"
    options = [option(str(v), f"{v}{labels_suffix if v == field.min else ''}") for v in values]
    elem: dict[str, Any] = {
        "type": "static_select",
        "action_id": field.name,
        "options": options,
    }
    if field.default is not None:
        match = next((o for o in options if o["value"] == str(field.default)), None)
        if match:
            elem["initial_option"] = match
    return elem
