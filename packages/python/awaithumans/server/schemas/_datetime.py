"""Datetime serialization mixin for response schemas.

SQLite stores datetimes as naive strings (no timezone suffix). When those
come back through SQLModel, Pydantic serializes them without a `Z`, and
the dashboard's `new Date(...)` parses them as *local* time — not UTC.
The result is up to ±14h of drift in "created … ago" labels depending
on the viewer's timezone.

All our stored timestamps ARE UTC (the service layer calls
`datetime.now(timezone.utc)`); SQLite just drops the tzinfo on the way
out. Fix at the serialization boundary: assume naive-in-DB means UTC,
and emit ISO strings with a `Z` suffix that `new Date(...)` interprets
correctly on every client.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime as ISO-8601 in UTC with a `Z` suffix.

    - Naive datetimes are assumed to be UTC (our write path guarantees
      this) and annotated before formatting.
    - Aware datetimes are converted to UTC.
    """
    if dt is None:
        return None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    # Python emits `+00:00`; the web convention is `Z`. Both are valid
    # ISO-8601, but `Z` is 5 bytes shorter and what `JSON.parse`-y
    # clients expect.
    return dt.isoformat().replace("+00:00", "Z")
