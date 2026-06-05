"""Schema presets exposed to demo visitors.

Each preset maps to a fixed Pydantic model definition. The v2 demo
does not expose a custom field editor; visitors pick one of these
presets and the AI extractor + reviewer work against that schema.
"""

from __future__ import annotations

from pydantic import BaseModel

from awaithumans.server.services.demo.schema_builder import (
    SchemaFieldSpec,
    SchemaSpec,
    build_pydantic_model,
)

PRESET_SPECS: dict[str, SchemaSpec] = {
    "Invoice": SchemaSpec(
        name="Invoice",
        fields=[
            SchemaFieldSpec(name="vendor", type="str"),
            SchemaFieldSpec(name="invoice_number", type="str"),
            SchemaFieldSpec(name="invoice_date", type="date"),
            SchemaFieldSpec(name="total_cents", type="int"),
        ],
    ),
    "Receipt": SchemaSpec(
        name="Receipt",
        fields=[
            SchemaFieldSpec(name="vendor", type="str"),
            SchemaFieldSpec(name="total_cents", type="int"),
            SchemaFieldSpec(name="date", type="date"),
        ],
    ),
    "ID": SchemaSpec(
        name="GovernmentID",
        fields=[
            SchemaFieldSpec(name="full_name", type="str"),
            SchemaFieldSpec(name="document_number", type="str"),
            SchemaFieldSpec(name="date_of_birth", type="date"),
            SchemaFieldSpec(name="expiry_date", type="date"),
        ],
    ),
    "Lease": SchemaSpec(
        name="Lease",
        fields=[
            SchemaFieldSpec(name="tenant_name", type="str"),
            SchemaFieldSpec(name="landlord_name", type="str"),
            SchemaFieldSpec(name="monthly_rent_cents", type="int"),
            SchemaFieldSpec(name="start_date", type="date"),
            SchemaFieldSpec(name="end_date", type="date"),
        ],
    ),
    "Resume": SchemaSpec(
        name="Resume",
        fields=[
            SchemaFieldSpec(name="candidate_name", type="str"),
            SchemaFieldSpec(name="email", type="str"),
            SchemaFieldSpec(name="years_experience", type="int"),
            SchemaFieldSpec(name="skills", type="list[str]"),
        ],
    ),
}


def build_preset_model(preset_key: str) -> type[BaseModel]:
    """Resolve a preset key to its Pydantic model class.

    Raises KeyError if the key is unknown. Callers should catch and
    map to a 400-class user-facing error (the route layer handles
    this via DemoSchemaError).
    """
    spec = PRESET_SPECS[preset_key]
    return build_pydantic_model(spec)
