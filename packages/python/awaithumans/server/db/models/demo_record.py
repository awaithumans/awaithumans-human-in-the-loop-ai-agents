"""DemoRecord, one row per /demo/start call.

Tracks email, IP hash, hot-lane flag, preset, per-field confidences,
pending fields, and partial reviewer corrections. The corresponding
AwaitVerify task lives in the ``tasks`` table; its id is stored here.
The polling endpoint reads field_confidences + pending_field_names +
field_corrections to drive the live in-session result UI.

``schema_spec``, ``provider``, and ``reviewer_result`` from the v1 design
are intentionally absent in v2: the provider is hardcoded, the schema
is determined by ``preset_key`` alone, and the reviewer's corrections
are stored field-by-field in ``field_corrections`` rather than as one
final blob.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlmodel import JSON, Column, Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now
from awaithumans.server.db.models.demo_status import DemoStatus


class DemoRecord(SQLModel, table=True):
    __tablename__ = "demo_records"

    id: str = Field(default_factory=new_id, primary_key=True)

    email: str = Field(index=True)
    email_domain: str = Field(index=True)
    ip_hash: str = Field(index=True)

    is_hot_demo: bool = Field(default=False)
    preset_key: str = Field(
        description="Schema preset selected by the visitor "
        "(Invoice / Receipt / ID / Lease / Resume).",
    )

    ai_result: dict[str, Any] | None = Field(
        sa_column=Column(JSON),
        default=None,
        description="Raw values dict returned by the AI extractor.",
    )
    field_confidences: dict[str, float] = Field(
        sa_column=Column(JSON),
        default_factory=dict,
        description="Per-field confidence score 0.0..1.0 from the provider.",
    )
    pending_field_names: list[str] = Field(
        sa_column=Column(JSON),
        default_factory=list,
        description="Fields below the confidence threshold; awaiting reviewer.",
    )
    field_corrections: dict[str, Any] = Field(
        sa_column=Column(JSON),
        default_factory=dict,
        description="Reviewer-confirmed values, populated field-by-field as "
        "the reviewer submits each one.",
    )

    awaitverify_task_id: str | None = Field(default=None, index=True)

    status: DemoStatus = Field(default=DemoStatus.EXTRACTING)

    cost_estimate_cents: int = Field(
        default=0,
        description="Per-call provider cost. Pre-call estimate, then post-call actual.",
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    email_sent_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )

    __table_args__ = (
        Index("ix_demo_records_email_created", "email", "created_at"),
        Index("ix_demo_records_ip_created", "ip_hash", "created_at"),
        Index("ix_demo_records_created_status", "created_at", "status"),
    )
