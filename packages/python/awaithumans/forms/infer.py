"""Type-based primitive inference for Pydantic fields without Annotated metadata.

Used as a fallback by extract_form(). When a developer hasn't picked a
primitive explicitly, we guess a reasonable one from the field type:

    bool                  → Switch
    str                   → ShortText
    int / float           → ShortText (subtype=number)
    datetime.date         → DatePicker
    datetime.datetime     → DateTimePicker
    datetime.time         → TimePicker
    Literal[a, b, c]      → SingleSelect with options from literal values
    Enum subclass         → SingleSelect with options from enum members
    list[Enum]            → MultiSelect with options from enum members
    anything else         → LongText (the developer should add Annotated)
"""

from __future__ import annotations

import enum
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from typing import Any, Literal, Union, get_args, get_origin

from awaithumans.forms.base import FormFieldBase
from awaithumans.forms.fields.date_time import (
    DatePicker,
    DateTimePicker,
    TimePicker,
)
from awaithumans.forms.fields.selection import (
    MultiSelect,
    SelectOption,
    SingleSelect,
    Switch,
)
from awaithumans.forms.fields.text import LongText, ShortText


def infer_field_from_type(
    name: str,
    annotation: Any,
    is_required: bool,
) -> FormFieldBase:
    """Pick a default primitive for a field whose type we can introspect."""
    inner = _unwrap_optional(annotation)
    origin = get_origin(inner)
    args = get_args(inner)
    label = _humanize(name)

    if inner is bool:
        return Switch(name=name, label=label, required=is_required)

    if inner is str:
        return ShortText(name=name, label=label, required=is_required)

    if inner is int or inner is float:
        return ShortText(name=name, label=label, required=is_required, subtype="number")

    if inner is _date:
        return DatePicker(name=name, label=label, required=is_required)

    if inner is _datetime:
        return DateTimePicker(name=name, label=label, required=is_required)

    if inner is _time:
        return TimePicker(name=name, label=label, required=is_required)

    if origin is Literal:
        options = [SelectOption(value=str(v), label=str(v)) for v in args]
        return SingleSelect(name=name, label=label, required=is_required, options=options)

    if isinstance(inner, type) and issubclass(inner, enum.Enum):
        options = _options_from_enum(inner)
        return SingleSelect(name=name, label=label, required=is_required, options=options)

    if origin is list and args:
        first = args[0]
        if isinstance(first, type) and issubclass(first, enum.Enum):
            options = _options_from_enum(first)
            return MultiSelect(name=name, label=label, required=is_required, options=options)

    return LongText(name=name, label=label, required=is_required)


def _options_from_enum(enum_cls: type[enum.Enum]) -> list[SelectOption]:
    return [
        SelectOption(
            value=str(m.value),
            label=m.name.replace("_", " ").title(),
        )
        for m in enum_cls
    ]


def _unwrap_optional(annotation: Any) -> Any:
    """Return X for Optional[X] / Union[X, None] / X | None; else annotation unchanged."""
    origin = get_origin(annotation)
    if origin is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]

    # Python 3.10+ PEP 604 unions (X | None) use types.UnionType.
    import types

    union_type = getattr(types, "UnionType", None)
    if union_type is not None and isinstance(annotation, union_type):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]

    return annotation


def _humanize(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()
