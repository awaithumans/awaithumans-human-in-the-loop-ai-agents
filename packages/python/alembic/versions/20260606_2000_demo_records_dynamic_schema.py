"""demo_records: drop preset_key, add schema_spec JSON.

The v3 demo lets the visitor edit an LLM-proposed Pydantic schema
rather than picking from five hard-coded presets. The DemoRecord now
persists the full ``schema_spec`` JSON (name + fields) instead of a
preset key. Old rows are migrated to ``{"name": preset_key, "fields": []}``
so the column stays NOT NULL without losing audit history. The lost
field list is fine because the AI result + confidences + corrections
all carry the actual extracted shape.

The backfill is done in Python (via a row-by-row UPDATE driven by the
bind) rather than dialect-specific JSON SQL so the migration works on
both SQLite (dev) and Postgres (prod).

Revision ID: 20260606_2000
Revises: 20260604_1200
Create Date: 2026-06-06 20:00:00
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "20260606_2000"
down_revision = "20260604_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("demo_records") as batch_op:
        batch_op.add_column(
            sa.Column(
                "schema_spec",
                sa.JSON(),
                nullable=True,
            )
        )

    # Backfill schema_spec from preset_key for any pre-existing rows so
    # the column can be flipped to NOT NULL. Reading the rows in Python
    # keeps this dialect-agnostic (SQLite has json_object, Postgres has
    # jsonb_build_object, neither shape works on the other). For the
    # demo we record the name and an empty fields array; the lost field
    # list is fine because ai_result / field_confidences carry the
    # actual extraction shape.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, preset_key FROM demo_records WHERE schema_spec IS NULL")
    ).fetchall()
    for row in rows:
        spec_json = json.dumps({"name": row.preset_key, "fields": []})
        bind.execute(
            sa.text("UPDATE demo_records SET schema_spec = :spec WHERE id = :id"),
            {"spec": spec_json, "id": row.id},
        )

    with op.batch_alter_table("demo_records") as batch_op:
        batch_op.alter_column(
            "schema_spec",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch_op.drop_column("preset_key")


def downgrade() -> None:
    with op.batch_alter_table("demo_records") as batch_op:
        batch_op.add_column(
            sa.Column(
                "preset_key",
                sa.String(),
                nullable=True,
            )
        )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, schema_spec FROM demo_records WHERE preset_key IS NULL")
    ).fetchall()
    for row in rows:
        spec = row.schema_spec
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except (json.JSONDecodeError, ValueError):
                spec = {}
        name = "Document"
        if isinstance(spec, dict):
            raw_name = spec.get("name")
            if isinstance(raw_name, str) and raw_name:
                name = raw_name
        bind.execute(
            sa.text("UPDATE demo_records SET preset_key = :name WHERE id = :id"),
            {"name": name, "id": row.id},
        )

    with op.batch_alter_table("demo_records") as batch_op:
        batch_op.alter_column(
            "preset_key",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.drop_column("schema_spec")
