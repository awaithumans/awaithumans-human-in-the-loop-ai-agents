"""extract_form() — walk a Pydantic model, build a FormDefinition.

Covers:
- Annotated FormField wins over type inference.
- `name` is always set from the attribute name (developer can't set it wrong).
- `required` comes from Pydantic's field requiredness (Optional / default value).
- Labels default to humanized attribute names when the developer omits one.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from awaithumans.forms import (
    LongText,
    Signature,
    Switch,
    extract_form,
    long_text,
    signature,
    switch,
)


class _WireApproval(BaseModel):
    approve: Annotated[bool, switch(label="Approve this wire?")]
    comment: Annotated[str | None, long_text(label="Reason")] = None
    # Optional field without any Annotated metadata — falls back to inference.
    notes: str | None = None


def test_annotated_primitives_are_used() -> None:
    form = extract_form(_WireApproval)
    approve = next(f for f in form.fields if f.name == "approve")
    assert isinstance(approve, Switch)
    assert approve.label == "Approve this wire?"
    assert approve.required is True  # no default → required

    comment = next(f for f in form.fields if f.name == "comment")
    assert isinstance(comment, LongText)
    assert comment.required is False  # default=None → optional


def test_unannotated_field_falls_back_to_inference() -> None:
    form = extract_form(_WireApproval)
    notes = next(f for f in form.fields if f.name == "notes")
    # str inferred → ShortText, but field is optional with default None.
    assert notes.kind == "short_text"
    assert notes.required is False


def test_label_defaults_to_humanized_name() -> None:
    class NoLabels(BaseModel):
        first_name: Annotated[str, signature()]
        approved_by_manager: Annotated[bool, switch()]

    form = extract_form(NoLabels)
    names = {f.name: f for f in form.fields}
    assert isinstance(names["first_name"], Signature)
    assert names["first_name"].label == "First Name"
    assert isinstance(names["approved_by_manager"], Switch)
    assert names["approved_by_manager"].label == "Approved By Manager"


def test_name_is_always_overridden_from_attribute() -> None:
    """Even if the DSL helper somehow carried a name, extract_form fills from the attribute."""

    class SomeModel(BaseModel):
        real_name: Annotated[bool, switch(label="x")]

    form = extract_form(SomeModel)
    assert form.fields[0].name == "real_name"


def test_required_driven_by_default_value() -> None:
    class MixedReq(BaseModel):
        must: Annotated[bool, switch(label="must")]
        optional_explicit: Annotated[bool, switch(label="opt")] = False
        optional_via_field: Annotated[bool, switch(label="opt2")] = Field(default=True)

    form = extract_form(MixedReq)
    required_map = {f.name: f.required for f in form.fields}
    assert required_map == {
        "must": True,
        "optional_explicit": False,
        "optional_via_field": False,
    }
