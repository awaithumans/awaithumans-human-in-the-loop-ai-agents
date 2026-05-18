"""Task statistics — aggregation for the Analytics page.

For MVP we pull rows within the lookback window and group in Python.
With a few thousand tasks/day this is still under 50 ms. When we
outgrow it, replace with a CTE that does the grouping server-side
(`SELECT DATE(created_at), COUNT(*) ... GROUP BY 1`).

All queries go through the existing indexes on `created_at` and
`status` — the window filter hits the b-tree in both SQLite and
Postgres.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import Task
from awaithumans.server.schemas.stats import TaskStats, TaskStatsByDay
from awaithumans.types import TaskStatus
from awaithumans.utils.constants import TERMINAL_STATUSES_SET


async def get_task_stats(session: AsyncSession, *, window_days: int = 30) -> TaskStats:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    totals = await _totals(session)
    window_rows = await _window_rows(session, window_start)

    by_day = _bucket_by_day(window_rows, now=now, window_days=window_days)
    by_channel = _bucket_by_channel(window_rows)
    avg_seconds = _avg_completion_seconds(window_rows)

    # Completion rate uses totals (all-time), matching operator intent:
    # "of tasks that ever finished, how many did a human actually complete".
    terminal_count = sum(totals.get(s.value, 0) for s in TERMINAL_STATUSES_SET)
    completed_count = totals.get(TaskStatus.COMPLETED.value, 0)
    completion_rate: float | None = completed_count / terminal_count if terminal_count > 0 else None

    return TaskStats(
        window_days=window_days,
        generated_at=now.isoformat(),
        totals=totals,
        completion_rate=completion_rate,
        avg_completion_seconds=avg_seconds,
        by_day=by_day,
        by_channel=by_channel,
    )


# ─── Queries ─────────────────────────────────────────────────────────────


async def _totals(session: AsyncSession) -> dict[str, int]:
    """All-time counts grouped by status."""
    result = await session.execute(select(Task.status))
    counter: Counter[str] = Counter()
    for (status,) in result.all():
        # SQLAlchemy returns the enum member; .value normalises to str.
        counter[_status_value(status)] += 1
    return dict(counter)


async def _window_rows(
    session: AsyncSession, window_start: datetime
) -> list[tuple[Any, datetime, datetime | None, str | None]]:
    """Rows inside the lookback window.

    Returns a list of (status, created_at, completed_at, completed_via_channel).
    Kept small (four columns) so serialising doesn't balloon the payload.
    """
    result = await session.execute(
        select(
            Task.status,
            Task.created_at,
            Task.completed_at,
            Task.completed_via_channel,
        ).where(Task.created_at >= window_start)
    )
    return list(result.all())


# ─── Grouping ────────────────────────────────────────────────────────────


def _bucket_by_day(
    rows: list[tuple[Any, datetime, datetime | None, str | None]],
    *,
    now: datetime,
    window_days: int,
) -> list[TaskStatsByDay]:
    """Created/completed counts per calendar day, UTC.

    Produces exactly `window_days` entries ending today — zero-filled so
    the chart renders a clean axis even on days with no traffic.
    """
    created: Counter[str] = Counter()
    completed: Counter[str] = Counter()

    for _status, created_at, completed_at, _ch in rows:
        created[created_at.date().isoformat()] += 1
        if completed_at is not None:
            completed[completed_at.date().isoformat()] += 1

    today = now.date()
    out: list[TaskStatsByDay] = []
    for offset in range(window_days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        out.append(
            TaskStatsByDay(
                date=d,
                created=created.get(d, 0),
                completed=completed.get(d, 0),
            )
        )
    return out


def _bucket_by_channel(
    rows: list[tuple[Any, datetime, datetime | None, str | None]],
) -> dict[str, int]:
    """Count completed tasks by `completed_via_channel`."""
    counter: Counter[str] = Counter()
    for status, _created_at, _completed_at, channel in rows:
        if _status_value(status) != TaskStatus.COMPLETED.value:
            continue
        counter[channel or "unknown"] += 1
    return dict(counter)


def _avg_completion_seconds(
    rows: list[tuple[Any, datetime, datetime | None, str | None]],
) -> float | None:
    """Mean elapsed seconds from created_at → completed_at over the window.

    Only completed tasks contribute. Returns None when nothing completed
    so the UI can render "—" rather than a misleading 0.
    """
    deltas: list[float] = []
    for status, created_at, completed_at, _ch in rows:
        if _status_value(status) != TaskStatus.COMPLETED.value:
            continue
        if completed_at is None:
            continue
        deltas.append((completed_at - created_at).total_seconds())
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


def _status_value(status: Any) -> str:
    """TaskStatus enum member or raw string — return the string value."""
    return status.value if hasattr(status, "value") else str(status)
