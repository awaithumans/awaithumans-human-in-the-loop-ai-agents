from __future__ import annotations

import datetime as dt

import pytest
from pydantic import BaseModel

from awaithumans.server.services.demo.exceptions import DemoSchemaError
from awaithumans.server.services.demo.schema_builder import (
    SchemaFieldSpec,
    SchemaSpec,
    build_pydantic_model,
    spec_from_json,
)


def test_builds_simple_model() -> None:
    spec = SchemaSpec(
        name="Receipt",
        fields=[
            SchemaFieldSpec(name="vendor", type="str"),
            SchemaFieldSpec(name="total_cents", type="int"),
        ],
    )
    Model = build_pydantic_model(spec)  # noqa: N806 -- dynamically created class
    inst = Model(vendor="Acme", total_cents=1299)
    assert inst.vendor == "Acme"
    assert inst.total_cents == 1299
    assert issubclass(Model, BaseModel)


def test_supports_all_primitive_types() -> None:
    spec = SchemaSpec(
        name="All",
        fields=[
            SchemaFieldSpec(name="s", type="str"),
            SchemaFieldSpec(name="i", type="int"),
            SchemaFieldSpec(name="f", type="float"),
            SchemaFieldSpec(name="b", type="bool"),
            SchemaFieldSpec(name="d", type="date"),
            SchemaFieldSpec(name="tags", type="list[str]"),
        ],
    )
    Model = build_pydantic_model(spec)  # noqa: N806 -- dynamically created class
    inst = Model(s="x", i=1, f=1.5, b=True, d=dt.date(2026, 1, 1), tags=["a", "b"])
    assert inst.tags == ["a", "b"]


def test_rejects_reserved_keyword_field() -> None:
    spec = SchemaSpec(
        name="Bad",
        fields=[SchemaFieldSpec(name="class", type="str")],
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_rejects_duplicate_field_names() -> None:
    spec = SchemaSpec(
        name="Bad",
        fields=[
            SchemaFieldSpec(name="x", type="str"),
            SchemaFieldSpec(name="x", type="int"),
        ],
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_rejects_unsupported_type() -> None:
    # Route through spec_from_json so Pydantic's Literal validation is
    # wrapped into DemoSchemaError per the unsupported-type contract.
    with pytest.raises(DemoSchemaError):
        spec_from_json(
            {
                "name": "Bad",
                "fields": [{"name": "x", "type": "bytes"}],
            }
        )


def test_rejects_invalid_model_name() -> None:
    spec = SchemaSpec(
        name="not a class",
        fields=[SchemaFieldSpec(name="x", type="str")],
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_rejects_empty_fields() -> None:
    spec = SchemaSpec(name="Empty", fields=[])
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_spec_from_json_round_trip() -> None:
    raw = {
        "name": "Invoice",
        "fields": [
            {"name": "vendor", "type": "str"},
            {"name": "total_cents", "type": "int"},
        ],
    }
    spec = spec_from_json(raw)
    assert spec.name == "Invoice"
    assert spec.fields[0].name == "vendor"


def test_spec_from_json_rejects_garbage() -> None:
    with pytest.raises(DemoSchemaError):
        spec_from_json({"fields": []})  # missing name
    with pytest.raises(DemoSchemaError):
        spec_from_json("not a dict")  # type: ignore[arg-type]


def test_record_field_builds_nested_model() -> None:
    """A `record` field validates a nested object with named sub-fields."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [
                {"name": "vendor", "type": "str"},
                {
                    "name": "totals",
                    "type": "record",
                    "fields": [
                        {"name": "subtotal_cents", "type": "int"},
                        {"name": "tax_cents", "type": "int"},
                    ],
                },
            ],
        }
    )
    Model = build_pydantic_model(spec)  # noqa: N806 -- dynamically created class
    inst = Model(
        vendor="Acme",
        totals={"subtotal_cents": 1000, "tax_cents": 80},
    )
    assert inst.vendor == "Acme"
    assert inst.totals.subtotal_cents == 1000
    assert inst.totals.tax_cents == 80


def test_list_record_builds_list_of_models() -> None:
    """A `list[record]` field validates a list of nested objects."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [
                {
                    "name": "line_items",
                    "type": "list[record]",
                    "fields": [
                        {"name": "description", "type": "str"},
                        {"name": "unit_cents", "type": "int"},
                    ],
                },
            ],
        }
    )
    Model = build_pydantic_model(spec)  # noqa: N806 -- dynamically created class
    inst = Model(
        line_items=[
            {"description": "Widget", "unit_cents": 100},
            {"description": "Gizmo", "unit_cents": 250},
        ]
    )
    assert len(inst.line_items) == 2
    assert inst.line_items[0].description == "Widget"
    assert inst.line_items[1].unit_cents == 250


def test_record_requires_fields() -> None:
    """A `record` field with no `fields` array fails validation."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [{"name": "totals", "type": "record"}],
        }
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_list_record_requires_fields() -> None:
    """Same rule applies to `list[record]`: empty fields fails."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [
                {"name": "line_items", "type": "list[record]", "fields": []},
            ],
        }
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_primitive_field_rejects_nested_fields() -> None:
    """A primitive field that carries a nested `fields` array fails."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [
                {
                    "name": "vendor",
                    "type": "str",
                    "fields": [{"name": "noise", "type": "str"}],
                },
            ],
        }
    )
    with pytest.raises(DemoSchemaError):
        build_pydantic_model(spec)


def test_nested_unsupported_type_raises() -> None:
    """A bogus nested type surfaces as a schema error, not a stack
    trace, via the same Literal validation path."""
    with pytest.raises(DemoSchemaError):
        spec_from_json(
            {
                "name": "Invoice",
                "fields": [
                    {
                        "name": "totals",
                        "type": "record",
                        "fields": [
                            {"name": "subtotal_cents", "type": "decimal"},
                        ],
                    },
                ],
            }
        )


def test_nested_record_inside_list_record() -> None:
    """Nesting is recursive: a `list[record]` row can itself contain
    a `record` child."""
    spec = spec_from_json(
        {
            "name": "Invoice",
            "fields": [
                {
                    "name": "line_items",
                    "type": "list[record]",
                    "fields": [
                        {"name": "description", "type": "str"},
                        {
                            "name": "pricing",
                            "type": "record",
                            "fields": [
                                {"name": "unit_cents", "type": "int"},
                                {"name": "quantity", "type": "int"},
                            ],
                        },
                    ],
                },
            ],
        }
    )
    Model = build_pydantic_model(spec)  # noqa: N806 -- dynamically created class
    inst = Model(
        line_items=[
            {
                "description": "Widget",
                "pricing": {"unit_cents": 100, "quantity": 3},
            },
        ]
    )
    assert inst.line_items[0].pricing.quantity == 3
