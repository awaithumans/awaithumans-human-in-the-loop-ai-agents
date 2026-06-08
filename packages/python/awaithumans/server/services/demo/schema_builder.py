"""Visual-builder JSON to Pydantic model.

Zero code injection: types come from a fixed allowlist, model and
field names are validated against `str.isidentifier()` and a Python
reserved-keyword denylist. No exec, no eval, no AST parsing.

Supports nested structures via two compound types:

- ``record``: a nested object with its own named fields (e.g. an
  address block, a totals summary).
- ``list[record]``: a list of nested objects (e.g. table rows of
  line items, employees, transaction logs).

Nested records can themselves contain ``record`` or ``list[record]``
fields, so the schema is arbitrarily deep. The validator walks the
tree, and `build_pydantic_model` recursively constructs nested
Pydantic models so the extractor's response validation handles the
full structure end-to-end.
"""

from __future__ import annotations

import datetime as dt
import keyword
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, create_model

from awaithumans.server.services.demo.exceptions import DemoSchemaError

SupportedType = Literal[
    "str",
    "int",
    "float",
    "bool",
    "date",
    "list[str]",
    "record",
    "list[record]",
]


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


def _coerce_int(value: Any) -> Any:
    """Tolerate numeric strings on `int` fields.

    Real documents render integers with thousands separators
    ("1,234"), currency symbols ("$1234", "€1.234,00"), trailing
    units ("30 días"), or signed prefixes. We strip non-digit
    decoration and pass the rest to int(). Floats coerce by
    truncation. Garbage passes through so Pydantic surfaces a clean
    error the reviewer can correct.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    # Pull out the first signed integer-looking substring after we
    # remove thousands separators. Handles "1,234.56" -> "1234" via
    # int(float(...)), "$1,200" -> 1200, "30 días" -> 30.
    cleaned = s.replace(",", "").replace(" ", "").replace("$", "").replace("€", "")
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        try:
            return int(float(cleaned))
        except (TypeError, ValueError):
            return value


def _coerce_float(value: Any) -> Any:
    """Tolerate numeric strings on `float` fields.

    Same defenses as `_coerce_int`. ``"1,151.34"`` -> 1151.34.
    ``"€1.234,00"`` (European decimal comma) is not auto-detected
    here; we strip the comma and let the period stand, so European-
    formatted values may need reviewer correction. That's acceptable
    for a v1 demo.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    cleaned = s.replace(",", "").replace(" ", "").replace("$", "").replace("€", "")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return value


_IntLike = Annotated[int, BeforeValidator(_coerce_int)]
_FloatLike = Annotated[float, BeforeValidator(_coerce_float)]

_PRIMITIVE_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": _IntLike,
    "float": _FloatLike,
    "bool": bool,
    "date": _DateLike,
    "list[str]": list[str],
}

# Type strings that introduce a nested model. Validation requires a
# non-empty ``fields`` array for these; all other types must not have
# ``fields`` set.
_NESTED_TYPES = ("record", "list[record]")

_MAX_NAME_LEN = 64


class SchemaFieldSpec(BaseModel):
    name: str
    type: SupportedType
    # Required when ``type`` is ``record`` or ``list[record]``; must be
    # absent or empty for primitive types. Recursive via PEP 563
    # `from __future__ import annotations`: the annotation is a string
    # at class-definition time, so the self-reference works without a
    # forward-ref literal.
    fields: list[SchemaFieldSpec] | None = None


class SchemaSpec(BaseModel):
    name: str
    fields: list[SchemaFieldSpec]


# Resolve the recursive forward reference now that SchemaFieldSpec is
# fully constructed. Without this, Pydantic raises on the first
# `model_validate` call against a payload that uses `record` /
# `list[record]` because the inner annotation is still a string under
# `from __future__ import annotations`.
SchemaFieldSpec.model_rebuild()


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

    Recursively constructs nested models for ``record`` and
    ``list[record]`` fields so the resulting class validates the full
    document tree, not just the top-level fields. Raises
    DemoSchemaError on any structural problem. Caller is responsible
    for catching to wrap in HTTP errors.
    """
    _validate_model_name(spec.name)
    _validate_fields(spec.fields)

    fields_for_create: dict[str, tuple[Any, Any]] = {}
    for field in spec.fields:
        py_type = _resolve_field_type(field)
        # Every field is Optional so the LLM can return ``null`` for
        # values it can't read confidently. Those become None on the
        # validated model, the orchestrator treats them as low-
        # confidence, and the reviewer fills them in. Without this,
        # one missing cell on a 30-field document fails the entire
        # extraction.
        fields_for_create[field.name] = (py_type | None, Field(default=None))

    model: type[BaseModel] = create_model(spec.name, **fields_for_create)  # type: ignore[call-overload]
    return model


def _resolve_field_type(field: SchemaFieldSpec) -> Any:
    """Turn one `SchemaFieldSpec` into the concrete Python type to
    hand to `create_model`.

    Primitive types come from `_PRIMITIVE_TYPE_MAP`. Nested types
    recursively build a child model via `build_pydantic_model` so
    every layer of the document tree gets validated.
    """
    if field.type in _PRIMITIVE_TYPE_MAP:
        return _PRIMITIVE_TYPE_MAP[field.type]
    if field.type == "record":
        nested = build_pydantic_model(
            SchemaSpec(name=_camel(field.name), fields=field.fields or [])
        )
        return nested
    if field.type == "list[record]":
        nested = build_pydantic_model(
            SchemaSpec(name=_camel(field.name), fields=field.fields or [])
        )
        return list[nested]  # type: ignore[valid-type]
    raise DemoSchemaError(f"Unsupported type: {field.type}")


def _camel(snake_name: str) -> str:
    """``"employees"`` to ``"Employee"``, ``"line_items"`` to ``"LineItem"``.

    Strips a trailing ``s`` (best-effort singularisation) and
    CamelCases the rest. Falls back to ``"Item"`` when the result
    isn't a valid Python identifier so `create_model` never crashes
    on a weird input name like ``"_"``.
    """
    parts = [p for p in snake_name.split("_") if p]
    if not parts:
        return "Item"
    camel = "".join(p.capitalize() for p in parts)
    if camel.endswith("s") and len(camel) > 1:
        camel = camel[:-1]
    if not camel.isidentifier() or keyword.iskeyword(camel):
        return "Item"
    return camel


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

        if field.type in _NESTED_TYPES:
            if not field.fields:
                raise DemoSchemaError(
                    f"Field {field.name!r} of type {field.type!r} requires a "
                    f"non-empty 'fields' array."
                )
            _validate_fields(field.fields)
        else:
            if field.fields:
                raise DemoSchemaError(
                    f"Field {field.name!r} of type {field.type!r} must not "
                    f"declare nested 'fields'."
                )
