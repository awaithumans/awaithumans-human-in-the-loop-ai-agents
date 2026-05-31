"""Audit trail model — one row per task state transition."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now


class AuditEntry(SQLModel, table=True):
    """Audit trail — one row per state transition."""

    __tablename__ = "audit_entries"

    id: str = Field(default_factory=new_id, primary_key=True)
    task_id: str = Field(index=True)

    # What happened
    from_status: str | None = Field(default=None)
    to_status: str
    action: str = Field(description="E.g., 'created', 'notified', 'completed', 'timed_out'")

    # Who did it
    actor_type: str = Field(description="'system', 'human', or 'agent'")
    actor_email: str | None = Field(default=None)

    # Context
    channel: str | None = Field(
        default=None,
        description="E.g., 'slack', 'email', 'dashboard', 'embed'",
    )
    extra_data: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)

    # Embed-token attribution. Both nullable; populated only when the
    # action came through an /embed/* request authenticated by a
    # short-lived embed JWT. `embed_sub` is whatever opaque identifier
    # the partner put in the token's `sub` claim ('acme:user_4271');
    # `embed_jti` is the unique token id, useful for revoking a leaked
    # token's effects. See spec §6.
    embed_sub: str | None = Field(default=None, max_length=256)
    embed_jti: str | None = Field(default=None, max_length=64)

    # When
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
