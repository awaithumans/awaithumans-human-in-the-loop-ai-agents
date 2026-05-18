"""Slack view_submission state → typed response dict."""

from __future__ import annotations

from awaithumans.forms import (
    FormDefinition,
    currency,
    date_picker,
    email,
    long_text,
    multi_select,
    opinion_scale,
    picture_choice,
    single_select,
    slider,
    star_rating,
    switch,
)
from awaithumans.server.channels.slack.coerce import slack_values_to_response
from awaithumans.utils.constants import SLACK_BLOCK_ID_PREFIX


def _name(field, n):
    return field.model_copy(update={"name": n})


def _state(**blocks):
    """Build a Slack view.state.values dict. Each kwarg: name=(action_dict)."""
    return {"values": {f"{SLACK_BLOCK_ID_PREFIX}{n}": {n: action} for n, action in blocks.items()}}


# ─── Per-primitive coercion ─────────────────────────────────────────────


def test_switch_true() -> None:
    form = FormDefinition(fields=[_name(switch(label="OK"), "ok")])
    resp = slack_values_to_response(
        form,
        _state(
            ok={
                "type": "radio_buttons",
                "selected_option": {"value": "true", "text": {"type": "plain_text", "text": "Yes"}},
            }
        ),
    )
    assert resp == {"ok": True}


def test_switch_false() -> None:
    form = FormDefinition(fields=[_name(switch(label="OK"), "ok")])
    resp = slack_values_to_response(
        form,
        _state(
            ok={
                "type": "radio_buttons",
                "selected_option": {"value": "false"},
            }
        ),
    )
    assert resp == {"ok": False}


def test_switch_blank_is_none() -> None:
    form = FormDefinition(fields=[_name(switch(label="OK"), "ok")])
    resp = slack_values_to_response(form, _state(ok={"type": "radio_buttons"}))
    assert resp == {"ok": None}


def test_short_text_email_and_blank() -> None:
    form = FormDefinition(fields=[_name(email(label="E"), "e")])
    assert slack_values_to_response(
        form, _state(e={"type": "email_text_input", "value": "a@b.c"})
    ) == {"e": "a@b.c"}
    assert slack_values_to_response(form, _state(e={"type": "email_text_input", "value": ""})) == {
        "e": None
    }


def test_currency_coerces_to_float() -> None:
    form = FormDefinition(fields=[_name(currency(currency_code="USD", label="Amt"), "amt")])
    resp = slack_values_to_response(form, _state(amt={"type": "number_input", "value": "123.45"}))
    assert resp == {"amt": 123.45}


def test_long_text() -> None:
    form = FormDefinition(fields=[_name(long_text(label="Why"), "why")])
    resp = slack_values_to_response(
        form,
        _state(why={"type": "plain_text_input", "value": "Looks good to me"}),
    )
    assert resp == {"why": "Looks good to me"}


def test_single_select() -> None:
    form = FormDefinition(fields=[_name(single_select(options=["a", "b", "c"], label="P"), "p")])
    resp = slack_values_to_response(
        form,
        _state(
            p={
                "type": "static_select",
                "selected_option": {"value": "b"},
            }
        ),
    )
    assert resp == {"p": "b"}


def test_multi_select_checkboxes() -> None:
    form = FormDefinition(fields=[_name(multi_select(options=["a", "b", "c"], label="P"), "p")])
    resp = slack_values_to_response(
        form,
        _state(
            p={
                "type": "checkboxes",
                "selected_options": [
                    {"value": "a"},
                    {"value": "c"},
                ],
            }
        ),
    )
    assert resp == {"p": ["a", "c"]}


def test_picture_choice_single_returns_list() -> None:
    form = FormDefinition(
        fields=[
            _name(
                picture_choice(options=[{"value": "a", "label": "A", "image_url": "x"}], label="P"),
                "p",
            )
        ]
    )
    resp = slack_values_to_response(
        form,
        _state(
            p={
                "type": "static_select",
                "selected_option": {"value": "a"},
            }
        ),
    )
    assert resp == {"p": ["a"]}


def test_date_picker() -> None:
    form = FormDefinition(fields=[_name(date_picker(label="When"), "when")])
    resp = slack_values_to_response(
        form,
        _state(when={"type": "datepicker", "selected_date": "2026-04-16"}),
    )
    assert resp == {"when": "2026-04-16"}


def test_slider_coerces_to_float() -> None:
    form = FormDefinition(fields=[_name(slider(min=0, max=10, label="Risk"), "risk")])
    resp = slack_values_to_response(form, _state(risk={"type": "number_input", "value": "7"}))
    assert resp == {"risk": 7.0}


def test_star_rating_coerces_to_int() -> None:
    form = FormDefinition(fields=[_name(star_rating(max=5, label="Q"), "q")])
    resp = slack_values_to_response(
        form,
        _state(
            q={
                "type": "static_select",
                "selected_option": {"value": "4"},
            }
        ),
    )
    assert resp == {"q": 4}


def test_opinion_scale_coerces_to_int() -> None:
    form = FormDefinition(fields=[_name(opinion_scale(min=1, max=10, label="NPS"), "nps")])
    resp = slack_values_to_response(
        form,
        _state(
            nps={
                "type": "static_select",
                "selected_option": {"value": "9"},
            }
        ),
    )
    assert resp == {"nps": 9}


def test_layout_fields_skipped() -> None:
    """Display/layout fields must not appear in the response dict."""
    from awaithumans.forms import display_text, section

    form = FormDefinition(
        fields=[
            section("Header"),
            display_text("Context"),
            _name(switch(label="OK"), "ok"),
        ]
    )
    resp = slack_values_to_response(
        form,
        _state(
            ok={
                "type": "radio_buttons",
                "selected_option": {"value": "true"},
            }
        ),
    )
    assert resp == {"ok": True}


def test_missing_block_yields_none() -> None:
    """Fields not submitted (e.g. optional) come back as None, not KeyError."""
    form = FormDefinition(
        fields=[
            _name(switch(label="OK"), "ok"),
            _name(long_text(label="Why"), "why"),
        ]
    )
    resp = slack_values_to_response(
        form,
        _state(
            ok={
                "type": "radio_buttons",
                "selected_option": {"value": "true"},
            }
        ),
    )
    assert resp == {"ok": True, "why": None}
