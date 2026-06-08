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

Date-like values are intentionally proposed as ``str`` by the
schema_proposer so the structured-outputs path sees the source format
intact. The reviewer downstream parses the string if required.
"""

from __future__ import annotations

import keyword
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from awaithumans.server.services.demo.exceptions import DemoSchemaError

SupportedType = Literal[
    "str",
    "int",
    "float",
    "bool",
    "list[str]",
    "record",
    "list[record]",
]


_PRIMITIVE_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
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
        # OpenAI structured outputs require every property to be in
        # the JSON Schema ``required`` array. Pydantic only marks a
        # field as required when there's no default, so we use
        # ``Field(...)``. The ``T | None`` union still lets the model
        # emit ``null`` for cells it can't read confidently; the
        # orchestrator treats nulls as low-confidence and routes them
        # to the reviewer.
        fields_for_create[field.name] = (py_type | None, Field(...))

    model: type[BaseModel] = create_model(
        spec.name,
        # ``extra="forbid"`` makes Pydantic emit ``additionalProperties:
        # false`` in the JSON Schema, which structured outputs also
        # requires on every object schema.
        __config__=ConfigDict(extra="forbid"),
        **fields_for_create,
    )  # type: ignore[call-overload]
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
