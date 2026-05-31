"""Outbound webhook delivery — one row per pending/failed/succeeded
attempt to POST a task's completion to its `callback_url`.

Why this table exists: deliveries used to be fire-and-forget (single
attempt, log on failure). For the durable-execution adapters that's
a real problem — a Temporal workflow waiting on a signal that never
arrives sits idle until its own timer fires, and operators reading
the dashboard see a `completed` task with no signal ever sent. With
this row we get persistent retry-with-backoff and a redrive endpoint.

Lifecycle:

  PENDING ─attempt fails──► PENDING (next_attempt_at advanced)
     │                          │
     │                       (>3 days)
     │                          ▼
     │                      ABANDONED
     │
     └─attempt succeeds──► SUCCEEDED

Concurrency: the dispatcher claims rows via an UPDATE…WHERE that pins
`next_attempt_at` to a future value before doing the POST, so two
schedulers running side by side can't double-send. SQLite serialises
writes anyway; Postgres relies on the row-level lock.

Body is stored as bytes because the HMAC signature is computed over
the exact byte sequence sent on the wire — re-serialising from a
JSON column at delivery time would change whitespace and break the
signature for receivers that pin the canonical form.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Index, LargeBinary
from sqlmodel import Column, Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now


class WebhookDeliveryStatus(str, enum.Enum):
    """Lifecycle states for an outbound webhook row."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    ABANDONED = "abandoned"


class WebhookDelivery(SQLModel, table=True):
    """One row per outbound webhook for a task's terminal transition.

    `body` and `signature` are computed at enqueue time from the task
    snapshot; later attempts re-send the exact same bytes so a
    receiver verifying HMAC sees a stable payload regardless of how
    many retries it took to land.

    `next_attempt_at` is the index for "what's due now?" — the
    scheduler's hot-path query is `WHERE status = 'pending' AND
    next_attempt_at <= now()`. The composite index covers it cheaply
    even when the table grows.
    """

    __tablename__ = "webhook_deliveries"

    id: str = Field(default_factory=new_id, primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True)
    url: str

    # Wire payload — the exact bytes the receiver gets. Stored as
    # `LargeBinary` so byte-exactness is preserved (a string column
    # could trigger encoding round-trips on some backends).
    body: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    signature: str

    status: WebhookDeliveryStatus = Field(default=WebhookDeliveryStatus.PENDING)

    # Bookkeeping for the retry loop. Timestamp columns are
    # tz-aware so the scheduler's `next_attempt_at <= now` comparison
    # against `datetime.now(timezone.utc)` doesn't trip asyncpg.
    # The composite (status, next_attempt_at) index lives in
    # `__table_args__` below, so we don't add a single-column index
    # here even though next_attempt_at is hot-path.
    attempt_count: int = Field(default=0)
    next_attempt_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    first_attempted_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
    last_attempt_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
    last_error: str | None = Field(default=None)
    last_status_code: int | None = Field(default=None)

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )

    __table_args__ = (
        # Composite index for the scheduler's hot-path scan.
        Index(
            "ix_webhook_deliveries_status_next_attempt_at",
            "status",
            "next_attempt_at",
        ),
    )
