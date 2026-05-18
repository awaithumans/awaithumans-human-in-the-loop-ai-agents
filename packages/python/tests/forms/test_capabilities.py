"""Channel capability matrix + form-level render support.

Every primitive kind must appear in CAPABILITIES for every channel — a missing
entry would make form_renders_in() crash. This test guards against that.
"""

from __future__ import annotations

from typing import get_args

from awaithumans.forms import (
    CAPABILITIES,
    Channel,
    FormDefinition,
    form_renders_in,
    long_text,
    section_collapse,
    short_text,
    signature,
    subform,
    switch,
    unsupported_fields,
)

# The kinds present in the discriminated union. Kept here as a hard-coded list
# so a missing primitive in CAPABILITIES is caught at test time.
ALL_KINDS = {
    "display_text",
    "short_text",
    "long_text",
    "rich_text",
    "switch",
    "single_select",
    "multi_select",
    "picture_choice",
    "slider",
    "star_rating",
    "opinion_scale",
    "ranking",
    "date",
    "datetime",
    "date_range",
    "time",
    "file_upload",
    "signature",
    "image",
    "video",
    "pdf_viewer",
    "html",
    "section",
    "divider",
    "section_collapse",
    "table",
    "subform",
}


def test_capabilities_cover_every_primitive_and_channel() -> None:
    channels = set(get_args(Channel))
    assert set(CAPABILITIES.keys()) == ALL_KINDS
    for kind, row in CAPABILITIES.items():
        assert set(row.keys()) == channels, f"{kind} missing channel(s)"


def test_simple_form_renders_everywhere() -> None:
    form = FormDefinition(fields=[switch(label="approve"), long_text(label="comment")])
    assert form_renders_in(form, "dashboard") is True
    assert form_renders_in(form, "slack") is True
    assert form_renders_in(form, "email_interactive") is False  # long_text → link-out in email
    assert unsupported_fields(form, "email_interactive") == ["comment"]


def test_signature_forces_link_out_in_slack_and_email() -> None:
    form = FormDefinition(fields=[signature(label="sign")])
    assert form_renders_in(form, "dashboard") is True
    assert form_renders_in(form, "slack") is False
    assert form_renders_in(form, "email_interactive") is False


def test_recursive_children_are_checked() -> None:
    # Subform contents must be checked even though subform itself is link-out in slack.
    form = FormDefinition(
        fields=[
            subform(fields=[signature(label="nested_sig")]),
        ]
    )
    # Both subform-itself and nested signature are link-out in slack; both offend.
    offenders = unsupported_fields(form, "slack")
    assert "nested_sig" in offenders


def test_section_collapse_children_checked() -> None:
    form = FormDefinition(
        fields=[
            section_collapse(
                "group", fields=[short_text(label="ok"), signature(label="inside_sig")]
            ),
        ]
    )
    offenders = unsupported_fields(form, "slack")
    # section_collapse itself is link-out in slack, AND the inner signature too.
    assert "inside_sig" in offenders
