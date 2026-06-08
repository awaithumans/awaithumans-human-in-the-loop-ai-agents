"""DemoRecord, one row per /demo/start call.

Tracks email, IP hash, hot-lane flag, the visitor's (possibly
LLM-proposed, possibly edited) schema spec, per-field confidences,
pending fields, and partial reviewer corrections. The corresponding
AwaitVerify task lives in the ``tasks`` table; its id is stored here.
The polling endpoint reads field_confidences + pending_field_names +
field_corrections to drive the live in-session result UI.

The schema is now visitor-controlled (proposed by an LLM pre-flight
call, then editable), so we persist the full ``schema_spec`` JSON
rather than a preset key. The ``schema_name`` property surfaces the
spec's CamelCase class name for use in task titles and emails.
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
    schema_spec: dict[str, Any] = Field(
        sa_column=Column(JSON),
        default_factory=dict,
        description="The Pydantic schema spec the extraction ran against "
        "(name + fields). Proposed by the LLM and (optionally) edited "
        "by the visitor before submit.",
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

    @property
    def schema_name(self) -> str:
        """The CamelCase class name from the persisted schema spec.

        Falls back to ``"Document"`` when the spec is missing a name so
        downstream task titles and emails never render an empty string.
        """
        name = self.schema_spec.get("name") if isinstance(self.schema_spec, dict) else None
        if isinstance(name, str) and name:
            return name
        return "Document"
