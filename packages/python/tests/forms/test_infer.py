"""Type-based primitive inference.

Covers the fallback path when a developer's Pydantic field has no Annotated
FormField attached. Inference should pick a sensible default for:

- bool / str / int / float
- date / datetime / time
- Literal[...] → SingleSelect
- Enum subclass → SingleSelect
- list[Enum] → MultiSelect
- Optional[X] is unwrapped to X
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel

from awaithumans.forms import (
    DatePicker,
    DateTimePicker,
    LongText,
    MultiSelect,
    ShortText,
    SingleSelect,
    Switch,
    TimePicker,
    extract_form,
)


def test_bool_infers_switch() -> None:
    class M(BaseModel):
        active: bool

    form = extract_form(M)
    assert isinstance(form.fields[0], Switch)
    assert form.fields[0].required is True


def test_str_infers_short_text() -> None:
    class M(BaseModel):
        name: str

    form = extract_form(M)
    assert isinstance(form.fields[0], ShortText)
    assert form.fields[0].subtype == "plain"


def test_int_float_infers_number_short_text() -> None:
    class M(BaseModel):
        count: int
        price: float

    form = extract_form(M)
    assert all(isinstance(f, ShortText) for f in form.fields)
    assert all(f.subtype == "number" for f in form.fields if isinstance(f, ShortText))


def test_date_types_infer_pickers() -> None:
    class M(BaseModel):
        d: date
        dt: datetime
        t: time

    form = extract_form(M)
    by_name = {f.name: f for f in form.fields}
    assert isinstance(by_name["d"], DatePicker)
    assert isinstance(by_name["dt"], DateTimePicker)
    assert isinstance(by_name["t"], TimePicker)


def test_literal_infers_single_select() -> None:
    class M(BaseModel):
        tier: Literal["bronze", "silver", "gold"]

    form = extract_form(M)
    f = form.fields[0]
    assert isinstance(f, SingleSelect)
    assert [o.value for o in f.options] == ["bronze", "silver", "gold"]


def test_enum_infers_single_select() -> None:
    class Tier(str, enum.Enum):
        BRONZE = "bronze"
        SILVER = "silver"
        GOLD = "gold"

    class M(BaseModel):
        tier: Tier

    form = extract_form(M)
    f = form.fields[0]
    assert isinstance(f, SingleSelect)
    assert [o.value for o in f.options] == ["bronze", "silver", "gold"]
    assert [o.label for o in f.options] == ["Bronze", "Silver", "Gold"]


def test_list_of_enum_infers_multi_select() -> None:
    class Tag(str, enum.Enum):
        RED = "red"
        BLUE = "blue"

    class M(BaseModel):
        tags: list[Tag]

    form = extract_form(M)
    f = form.fields[0]
    assert isinstance(f, MultiSelect)
    assert [o.value for o in f.options] == ["red", "blue"]


def test_optional_fields_are_not_required() -> None:
    class M(BaseModel):
        name: str | None = None
        active: bool | None = None

    form = extract_form(M)
    assert all(f.required is False for f in form.fields)


def test_unknown_type_falls_back_to_long_text() -> None:
    class Weird:
        pass

    class M(BaseModel):
        blob: Weird

        model_config = {"arbitrary_types_allowed": True}

    form = extract_form(M)
    assert isinstance(form.fields[0], LongText)
