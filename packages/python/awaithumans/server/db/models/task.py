"""Task model — one row per awaitHuman() call."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, text
from sqlmodel import JSON, Column, Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now
from awaithumans.types import TaskStatus
from awaithumans.utils.constants import TERMINAL_STATUSES_SET

# Partial unique index — only ACTIVE tasks have unique idempotency keys.
# Terminal rows are excluded from the index so the application-layer
# lookup (`_find_task_by_idempotency_key`) can return ANY task with a
# given key — including terminal ones — for the resumable-direct-mode
# recovery path: an agent that crashes during a human review and
# re-invokes `await_human()` with the same key gets back the stored
# response instead of creating a duplicate. The partial index is kept
# purely for race safety on concurrent INSERTs of *new* keys; in
# practice it never fires on the recovery path because the lookup
# returns the existing row before any INSERT is attempted.
#
# SQLAlchemy stores enum columns as the enum's NAME (uppercase),
# not .value (lowercase). The WHERE clause has to match, or the
# partial index never filters anything. Derive the names from
# TERMINAL_STATUSES_SET so this can't drift.
_TERMINAL_STATUS_VALUES = (
    "("
    + ", ".join(f"'{s.name}'" for s in sorted(TERMINAL_STATUSES_SET, key=lambda s: s.name))
    + ")"
)
_ACTIVE_IDEMPOTENCY_WHERE = f"status NOT IN {_TERMINAL_STATUS_VALUES}"


class Task(SQLModel, table=True):
    """Core task record — one row per awaitHuman() call."""

    __tablename__ = "tasks"

    id: str = Field(default_factory=new_id, primary_key=True)
    idempotency_key: str = Field(index=True)

    # Task description
    task: str = Field(description="Human-readable task description.")
    payload: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    payload_schema: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    response_schema: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    form_definition: dict[str, Any] | None = Field(
        sa_column=Column(JSON),
        default=None,
        description="Form primitive tree extracted from response_schema. Rendered per channel.",
    )

    # State
    status: TaskStatus = Field(default=TaskStatus.CREATED)

    # Routing
    assign_to: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)
    assigned_to_email: str | None = Field(default=None, index=True)
    # Resolved user ID after the task router picks a match. Stable across
    # email changes and populated even for Slack-only users (where
    # `assigned_to_email` stays null). Correlates tasks to the user
    # directory without requiring an email lookup.
    assigned_to_user_id: str | None = Field(default=None, index=True)

    # Notification
    notify: list[str] | None = Field(sa_column=Column(JSON), default=None)

    # Caller-supplied free-form metadata. Surfaced to reviewers in
    # Slack notifications, the review modal, and the dashboard task
    # detail panel — context the agent code wants the reviewer to
    # see when deciding how to complete the task. The shape is
    # ``dict[str, str]`` deliberately (not ``dict[str, Any]``): the
    # rendering surfaces are key-value text labels, and nested
    # structures invite reviewers to chase irrelevant detail. JSON
    # numbers / arrays / objects supplied here are still accepted
    # at the DB layer (SQLite is permissive), but the SDK + schema
    # coerce values to strings before they arrive.
    task_metadata: dict[str, str] | None = Field(sa_column=Column(JSON), default=None)

    # Pre-computed extraction (AwaitVerify Flow A / Flow B). Optional
    # JSON snapshot matching `response_schema` shape; the dashboard
    # mounts the form with these values pre-populated so the reviewer
    # verifies rather than re-types. Persisted verbatim — managed
    # validates against `response_schema` before forwarding, so the
    # OSS server doesn't re-validate.
    initial_response: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)

    # Response
    response: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)

    # Verification
    verifier_config: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)
    verifier_result: dict[str, Any] | None = Field(sa_column=Column(JSON), default=None)
    verification_attempt: int = Field(default=0)

    # Timeout
    timeout_seconds: int
    redact_payload: bool = Field(default=False)

    # Timestamps. All use `TIMESTAMP WITH TIME ZONE` via
    # tz_timestamp_column so the timeout-scheduler query
    # `Task.timeout_at <= datetime.now(timezone.utc)` doesn't trip
    # asyncpg's naive-vs-aware bind check on Postgres.
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
    timed_out_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
    timeout_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True, index=True),
        description="Pre-computed: created_at + timeout_seconds. Used by timeout scheduler.",
    )

    # Webhook callback
    callback_url: str | None = Field(default=None)

    # Metadata
    completed_by_email: str | None = Field(default=None)
    # Resolved user_id of whoever completed the task. Mirrors
    # `assigned_to_user_id` so a Slack-only completer (no email) is
    # still identifiable in the audit log. Populated alongside
    # `completed_by_email`; either or both can be null when the
    # completer isn't in the directory (raw admin-bearer call).
    completed_by_user_id: str | None = Field(default=None, index=True)
    completed_via_channel: str | None = Field(default=None)

    __table_args__ = (
        Index(
            "ix_tasks_active_idempotency_key",
            "idempotency_key",
            unique=True,
            sqlite_where=text(_ACTIVE_IDEMPOTENCY_WHERE),
            postgresql_where=text(_ACTIVE_IDEMPOTENCY_WHERE),
        ),
    )
