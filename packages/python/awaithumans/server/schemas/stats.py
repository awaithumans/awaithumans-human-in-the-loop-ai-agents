"""Task statistics schemas."""

from __future__ import annotations

from pydantic import BaseModel


class TaskStatsByDay(BaseModel):
    date: str  # YYYY-MM-DD
    created: int
    completed: int


class TaskStats(BaseModel):
    """Aggregated stats for the Analytics page.

    `window_days` is the lookback used for `by_day` / `by_channel`.
    `totals` is current counts across all time (not restricted to
    the window) — operators want to see the real task queue size,
    not just "tasks in the last 30 days".
    """

    window_days: int
    generated_at: str
    # status value → count (all-time)
    totals: dict[str, int]
    # completed / terminal; None when no terminal tasks yet
    completion_rate: float | None
    avg_completion_seconds: float | None
    by_day: list[TaskStatsByDay]
    # completed_via_channel → count (completed tasks in window only)
    by_channel: dict[str, int]
