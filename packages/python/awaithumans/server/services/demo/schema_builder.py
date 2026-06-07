"""Visual-builder JSON to Pydantic model.

Zero code injection: types come from a fixed allowlist, model and
field names are validated against `str.isidentifier()` and a Python
reserved-keyword denylist. No exec, no eval, no AST parsing.
"""

from __future__ import annotations

import datetime as dt
import keyword
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, create_model

from awaithumans.server.services.demo.exceptions import DemoSchemaError

SupportedType = Literal["str", "int", "float", "bool", "date", "list[str]"]


def _coerce_date(value: Any) -> Any:
    """Tolerate messy date strings on `date` fields.

    The demo extractor's LLM returns whatever date format appears on
    the document: ISO ("2026-01-15"), full ISO datetime
    ("2026-01-15T00:00:00Z"), localised forms like "16/feb./2024", and
    free text like "Jan 15, 2026". Pydantic's strict ISO parser
    rejects most of these. We hand them to dateutil with `fuzzy=True`
    and emit a real `datetime.date`. Strings that even dateutil can't
    parse pass through unchanged so Pydantic surfaces a clean error
    that the reviewer can correct.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    try:
        from dateutil import parser as _du_parser  # noqa: PLC0415

        return _du_parser.parse(s, fuzzy=True).date()
    except (ValueError, ImportError, TypeError, OverflowError):
        return value


_DateLike = Annotated[dt.date, BeforeValidator(_coerce_date)]

_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "date": _DateLike,
    "list[str]": list[str],
}

_MAX_FIELDS = 32
_MAX_NAME_LEN = 64


class SchemaFieldSpec(BaseModel):
    name: str
    type: SupportedType


class SchemaSpec(BaseModel):
    name: str
    fields: list[SchemaFieldSpec]


def spec_from_json(raw: Any) -> SchemaSpec:
    """Validate untrusted JSON and return a `SchemaSpec`.

    Wraps Pydantic's validation errors in `DemoSchemaError` so the
    centralized handler returns 400 with our copy.
    """
    if not isinstance(raw, dict):
        raise DemoSchemaError("Schema spec must be a JSON object.")
    try:
        return SchemaSpec.model_validate(raw)
    except Exception as exc:
        raise DemoSchemaError(f"Invalid schema spec: {exc}") from exc


def build_pydantic_model(spec: SchemaSpec) -> type[BaseModel]:
    """Build a concrete Pydantic model class from the spec.

    Raises DemoSchemaError on any structural problem. Caller is
    responsible for catching to wrap in HTTP errors.
    """
    _validate_model_name(spec.name)
    _validate_fields(spec.fields)

    fields_for_create: dict[str, tuple[Any, Any]] = {}
    for field in spec.fields:
        py_type = _TYPE_MAP.get(field.type)
        if py_type is None:
            raise DemoSchemaError(f"Unsupported type: {field.type}")
        fields_for_create[field.name] = (py_type, Field(...))

    model: type[BaseModel] = create_model(spec.name, **fields_for_create)  # type: ignore[call-overload]
    return model


def _validate_model_name(name: str) -> None:
    if not name or len(name) > _MAX_NAME_LEN:
        raise DemoSchemaError("Model name must be 1..64 characters.")
    if not name.isidentifier():
        raise DemoSchemaError(f"Model name {name!r} is not a valid identifier.")
    if keyword.iskeyword(name):
        raise DemoSchemaError(f"Model name {name!r} is a reserved keyword.")


def _validate_fields(fields: list[SchemaFieldSpec]) -> None:
    if not fields:
        raise DemoSchemaError("Schema must declare at least one field.")
    if len(fields) > _MAX_FIELDS:
        raise DemoSchemaError(f"Schema must declare at most {_MAX_FIELDS} fields.")
    seen: set[str] = set()
    for field in fields:
        if not field.name or len(field.name) > _MAX_NAME_LEN:
            raise DemoSchemaError(f"Field name must be 1..{_MAX_NAME_LEN} characters.")
        if not field.name.isidentifier():
            raise DemoSchemaError(f"Field name {field.name!r} is not a valid identifier.")
        if keyword.iskeyword(field.name):
            raise DemoSchemaError(f"Field name {field.name!r} is a reserved keyword.")
        if field.name in seen:
            raise DemoSchemaError(f"Duplicate field name: {field.name}.")
        seen.add(field.name)
