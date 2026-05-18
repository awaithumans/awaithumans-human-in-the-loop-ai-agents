"""Primitive construction and JSON roundtrip.

Every primitive should:
1. Be constructible via its DSL helper.
2. Serialize to JSON with a stable `kind` discriminator.
3. Deserialize back through FormDefinition without loss.
"""

from __future__ import annotations

import pytest

from awaithumans.forms import (
    FormDefinition,
    SectionCollapse,
    SelectOption,
    Subform,
    TableColumn,
    currency,
    date_picker,
    date_range,
    datetime_picker,
    # Helpers
    display_text,
    divider,
    email,
    file_upload,
    html,
    image,
    long_text,
    multi_select,
    opinion_scale,
    password,
    pdf_viewer,
    phone,
    picture_choice,
    ranking,
    rich_text,
    section,
    section_collapse,
    short_text,
    signature,
    single_select,
    slider,
    star_rating,
    subform,
    switch,
    table,
    time_picker,
    url,
    video,
)


def _roundtrip(form: FormDefinition) -> FormDefinition:
    """Serialize to JSON and parse back through FormDefinition."""
    payload = form.model_dump_json()
    return FormDefinition.model_validate_json(payload)


def test_every_primitive_roundtrips() -> None:
    form = FormDefinition(
        fields=[
            display_text("Context only"),
            short_text(label="Name"),
            email(label="Email"),
            url(label="Site"),
            phone(label="Phone"),
            currency(currency_code="USD", label="Amount"),
            password(label="Password"),
            long_text(label="Description"),
            rich_text(label="Notes"),
            switch(label="Active?"),
            single_select(options=[("a", "A"), ("b", "B")], label="Pick one"),
            multi_select(options=["x", "y"], label="Pick many"),
            picture_choice(
                options=[{"value": "a", "label": "A", "image_url": "http://x/a.png"}],
                label="Pictures",
            ),
            slider(min=0, max=10, label="Risk"),
            star_rating(max=5, label="Quality"),
            opinion_scale(min=1, max=10, label="NPS"),
            ranking(options=["one", "two", "three"], label="Order"),
            date_picker(label="When"),
            datetime_picker(label="Exactly when"),
            date_range(label="Range"),
            time_picker(label="Time"),
            file_upload(label="Upload"),
            signature(label="Sign"),
            image("http://x/y.png", label="Image"),
            video("http://x/y.mp4", label="Video"),
            pdf_viewer("http://x/y.pdf", label="Doc"),
            html("<b>hi</b>", label="HTML"),
            section("Section header"),
            divider(),
            section_collapse(
                "Collapsible",
                fields=[short_text(label="Inside", hint="nested")],
            ),
            table(
                columns=[
                    TableColumn(name="item", label="Item"),
                    TableColumn(name="qty", label="Qty", kind="number"),
                ],
                label="Line items",
            ),
            subform(
                fields=[short_text(label="Ref"), switch(label="Approved")],
                label="Multiple refs",
            ),
        ]
    )

    parsed = _roundtrip(form)
    assert len(parsed.fields) == len(form.fields)
    kinds = [f.kind for f in parsed.fields]
    assert kinds == [f.kind for f in form.fields]


def test_subtypes_on_short_text() -> None:
    """email()/url()/phone()/currency()/password() all produce ShortText with correct subtype."""
    assert email().subtype == "email"
    assert url().subtype == "url"
    assert phone().subtype == "phone"
    c = currency(currency_code="USD")
    assert c.subtype == "currency"
    assert c.currency_code == "USD"
    assert password().subtype == "password"


def test_options_accept_tuples_and_strings() -> None:
    """single_select/multi_select/ranking accept SelectOption, (value, label), or str."""
    s = single_select(options=[SelectOption(value="a", label="Alpha"), ("b", "Beta"), "c"])
    assert [o.value for o in s.options] == ["a", "b", "c"]
    assert [o.label for o in s.options] == ["Alpha", "Beta", "c"]


def test_unknown_option_type_raises() -> None:
    with pytest.raises(TypeError):
        single_select(options=[123])  # type: ignore[list-item]


def test_discriminator_enforced() -> None:
    """Deserialization fails if `kind` is wrong or missing."""
    bad = '{"version": 1, "fields": [{"kind": "nonexistent", "name": "x"}]}'
    # Pydantic raises ValidationError but we only care that SOMETHING
    # bubbles up — the model_validate_json contract is "fail loudly on
    # bad input." Pinning ValidationError ties this test to a Pydantic
    # implementation detail with no reader benefit.
    with pytest.raises(Exception):  # noqa: B017
        FormDefinition.model_validate_json(bad)


def test_recursive_section_collapse() -> None:
    form = FormDefinition(
        fields=[
            section_collapse(
                "Outer",
                fields=[
                    short_text(label="a"),
                    section_collapse("Inner", fields=[switch(label="b")]),
                ],
            )
        ]
    )
    parsed = _roundtrip(form)
    outer = parsed.fields[0]
    assert isinstance(outer, SectionCollapse)
    inner = outer.fields[1]
    assert isinstance(inner, SectionCollapse)
    assert inner.fields[0].kind == "switch"


def test_recursive_subform() -> None:
    form = FormDefinition(
        fields=[
            subform(
                fields=[
                    short_text(label="ref"),
                    subform(fields=[switch(label="ok")]),
                ]
            )
        ]
    )
    parsed = _roundtrip(form)
    outer = parsed.fields[0]
    assert isinstance(outer, Subform)
    inner = outer.fields[1]
    assert isinstance(inner, Subform)
    assert inner.fields[0].kind == "switch"
