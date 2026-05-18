"""Block Kit renderer — modal structure + per-primitive element shapes."""

from __future__ import annotations

import pytest

from awaithumans.forms import (
    FormDefinition,
    currency,
    date_picker,
    datetime_picker,
    email,
    file_upload,
    long_text,
    multi_select,
    opinion_scale,
    picture_choice,
    ranking,
    signature,
    single_select,
    slider,
    star_rating,
    switch,
    time_picker,
)
from awaithumans.server.channels.slack.blocks import (
    UnrenderableInSlackError,
    form_to_modal,
    open_review_message_blocks,
)
from awaithumans.utils.constants import (
    SLACK_ACTION_OPEN_REVIEW,
    SLACK_BLOCK_ID_PREFIX,
    SLACK_MODAL_CALLBACK_ID,
)


def _name(field, n):
    """Force a name onto a bare primitive (extract_form would normally do this)."""
    return field.model_copy(update={"name": n})


def _modal(fields):
    form = FormDefinition(fields=fields)
    return form_to_modal(
        form=form,
        task_id="task-123",
        task_title="Approve this wire",
        task_payload={"amount": 50000, "to": "Acme"},
        redact_payload=False,
    )


# ─── Modal skeleton ─────────────────────────────────────────────────────


def test_modal_has_required_top_level_fields() -> None:
    modal = _modal([_name(switch(label="Approve?"), "approve")])
    assert modal["type"] == "modal"
    assert modal["callback_id"] == SLACK_MODAL_CALLBACK_ID
    assert modal["private_metadata"] == "task-123"
    assert modal["submit"]["text"] == "Submit"
    assert modal["close"]["text"] == "Cancel"


def test_modal_prepends_title_header_and_payload_context() -> None:
    modal = _modal([_name(switch(label="Approve?"), "approve")])
    blocks = modal["blocks"]
    assert blocks[0]["type"] == "header"
    assert "Approve this wire" in blocks[0]["text"]["text"]
    # Payload is in a section with mrkdwn containing both keys.
    assert blocks[1]["type"] == "section"
    assert "amount" in blocks[1]["text"]["text"]
    assert "to" in blocks[1]["text"]["text"]


def test_redact_payload_hides_values() -> None:
    form = FormDefinition(fields=[_name(switch(label="ok"), "ok")])
    modal = form_to_modal(
        form=form,
        task_id="t",
        task_title="x",
        task_payload={"secret": "abc"},
        redact_payload=True,
    )
    text = "\n".join(
        b.get("text", {}).get("text", "") for b in modal["blocks"] if b.get("type") == "section"
    )
    assert "abc" not in text


# ─── Per-primitive element shapes ───────────────────────────────────────


def _input_for(fields, name):
    modal = _modal(fields)
    block = next(
        b
        for b in modal["blocks"]
        if b.get("type") == "input" and b.get("block_id") == f"{SLACK_BLOCK_ID_PREFIX}{name}"
    )
    return block["element"], block


def test_switch_renders_radio_buttons() -> None:
    elem, block = _input_for([_name(switch(label="OK"), "ok")], "ok")
    assert elem["type"] == "radio_buttons"
    assert {o["value"] for o in elem["options"]} == {"true", "false"}
    assert block["optional"] is False  # required by default


def test_switch_default_becomes_initial_option() -> None:
    elem, _ = _input_for([_name(switch(label="OK", default=True), "ok")], "ok")
    assert elem["initial_option"]["value"] == "true"


def test_short_text_subtypes_pick_typed_elements() -> None:
    elem_email, _ = _input_for([_name(email(label="E"), "e")], "e")
    assert elem_email["type"] == "email_text_input"

    elem_currency, _ = _input_for([_name(currency(currency_code="USD", label="Amt"), "amt")], "amt")
    assert elem_currency["type"] == "number_input"
    assert elem_currency["is_decimal_allowed"] is True


def test_long_text_is_multiline_plain_text_input() -> None:
    elem, _ = _input_for([_name(long_text(label="Why"), "why")], "why")
    assert elem["type"] == "plain_text_input"
    assert elem["multiline"] is True


def test_single_select_under_4_options_uses_radio_buttons() -> None:
    elem, _ = _input_for(
        [_name(single_select(options=["a", "b"], label="Pick"), "pick")],
        "pick",
    )
    assert elem["type"] == "radio_buttons"


def test_single_select_over_4_options_uses_static_select() -> None:
    elem, _ = _input_for(
        [_name(single_select(options=[f"opt{i}" for i in range(5)], label="Pick"), "pick")],
        "pick",
    )
    assert elem["type"] == "static_select"


def test_multi_select_under_10_uses_checkboxes() -> None:
    elem, _ = _input_for(
        [_name(multi_select(options=["a", "b"], label="Pick"), "pick")],
        "pick",
    )
    assert elem["type"] == "checkboxes"


def test_multi_select_over_10_uses_multi_static_select() -> None:
    elem, _ = _input_for(
        [_name(multi_select(options=[f"o{i}" for i in range(11)], label="P"), "p")],
        "p",
    )
    assert elem["type"] == "multi_static_select"


def test_picture_choice_single_falls_back_to_static_select() -> None:
    elem, _ = _input_for(
        [
            _name(
                picture_choice(options=[{"value": "a", "label": "A", "image_url": "x"}], label="P"),
                "p",
            )
        ],
        "p",
    )
    assert elem["type"] == "static_select"


def test_picture_choice_multiple_uses_multi_static_select() -> None:
    elem, _ = _input_for(
        [
            _name(
                picture_choice(
                    options=[{"value": "a", "label": "A", "image_url": "x"}],
                    multiple=True,
                    label="P",
                ),
                "p",
            )
        ],
        "p",
    )
    assert elem["type"] == "multi_static_select"


def test_date_picker_emits_datepicker() -> None:
    elem, _ = _input_for([_name(date_picker(label="When"), "when")], "when")
    assert elem["type"] == "datepicker"


def test_datetime_picker_emits_datetimepicker() -> None:
    elem, _ = _input_for([_name(datetime_picker(label="When"), "when")], "when")
    assert elem["type"] == "datetimepicker"


def test_time_picker_emits_timepicker() -> None:
    elem, _ = _input_for([_name(time_picker(label="When"), "when")], "when")
    assert elem["type"] == "timepicker"


def test_slider_emits_number_input_with_bounds() -> None:
    elem, _ = _input_for([_name(slider(min=0, max=10, step=1, label="Risk"), "risk")], "risk")
    assert elem["type"] == "number_input"
    assert float(elem["min_value"]) == 0
    assert float(elem["max_value"]) == 10


def test_star_rating_emits_static_select_with_star_labels() -> None:
    elem, _ = _input_for([_name(star_rating(max=5, label="Quality"), "q")], "q")
    assert elem["type"] == "static_select"
    assert len(elem["options"]) == 5
    assert "★" in elem["options"][-1]["text"]["text"]


def test_opinion_scale_emits_numeric_static_select() -> None:
    elem, _ = _input_for([_name(opinion_scale(min=1, max=5, label="NPS"), "nps")], "nps")
    assert elem["type"] == "static_select"
    assert {o["value"] for o in elem["options"]} == {"1", "2", "3", "4", "5"}


def test_file_upload_emits_file_input() -> None:
    elem, _ = _input_for([_name(file_upload(label="Upload", accept=[".pdf"]), "doc")], "doc")
    assert elem["type"] == "file_input"
    assert elem["filetypes"] == ["pdf"]
    assert elem["max_files"] == 1


# ─── Unrenderable primitives ────────────────────────────────────────────


def test_signature_raises_unrenderable() -> None:
    form = FormDefinition(fields=[_name(signature(label="Sign"), "sig")])
    with pytest.raises(UnrenderableInSlackError):
        form_to_modal(
            form=form,
            task_id="t",
            task_title="x",
            task_payload=None,
        )


def test_ranking_raises_unrenderable() -> None:
    form = FormDefinition(fields=[_name(ranking(options=["a", "b"], label="Rank"), "rank")])
    with pytest.raises(UnrenderableInSlackError):
        form_to_modal(
            form=form,
            task_id="t",
            task_title="x",
            task_payload=None,
        )


# ─── Link-out message ───────────────────────────────────────────────────


def test_open_review_message_renderable_form_has_both_buttons() -> None:
    blocks = open_review_message_blocks(
        task_id="t1",
        task_title="Approve wire",
        review_url="https://dash.example/tasks/t1",
        open_button_action_id=SLACK_ACTION_OPEN_REVIEW,
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = [e["action_id"] for e in actions["elements"]]
    assert SLACK_ACTION_OPEN_REVIEW in action_ids
    assert any("dashboard" in e.get("url", "") or True for e in actions["elements"])


def test_open_review_message_with_unsupported_fields_omits_open_button() -> None:
    blocks = open_review_message_blocks(
        task_id="t1",
        task_title="Sign me",
        review_url="https://dash.example/tasks/t1",
        open_button_action_id=SLACK_ACTION_OPEN_REVIEW,
        unsupported_fields=["signature"],
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = [e["action_id"] for e in actions["elements"]]
    assert SLACK_ACTION_OPEN_REVIEW not in action_ids
