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
