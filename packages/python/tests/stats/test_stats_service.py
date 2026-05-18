"""Task-stats aggregation — totals, completion rate, by-day bucket, channels."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import Task
from awaithumans.server.services.stats_service import get_task_stats
from awaithumans.types import TaskStatus

_key_seq = itertools.count()


def _task(
    *,
    created_at: datetime,
    status: TaskStatus = TaskStatus.CREATED,
    completed_at: datetime | None = None,
    channel: str | None = None,
    task: str = "do the thing",
) -> Task:
    return Task(
        idempotency_key=f"k-{next(_key_seq)}",
        task=task,
        payload={},
        payload_schema={},
        response_schema={},
        timeout_seconds=900,
        status=status,
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
        completed_via_channel=channel,
    )


@pytest.mark.asyncio
async def test_empty_stats(session: AsyncSession) -> None:
    stats = await get_task_stats(session, window_days=7)
    assert stats.window_days == 7
    assert stats.totals == {}
    assert stats.completion_rate is None
    assert stats.avg_completion_seconds is None
    assert len(stats.by_day) == 7  # zero-filled
    assert all(d.created == 0 and d.completed == 0 for d in stats.by_day)
    assert stats.by_channel == {}


@pytest.mark.asyncio
async def test_totals_count_all_statuses(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(created_at=now, status=TaskStatus.CREATED),
            _task(created_at=now, status=TaskStatus.CREATED),
            _task(created_at=now, status=TaskStatus.COMPLETED, completed_at=now),
            _task(created_at=now, status=TaskStatus.TIMED_OUT),
            _task(created_at=now, status=TaskStatus.CANCELLED),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    assert stats.totals["created"] == 2
    assert stats.totals["completed"] == 1
    assert stats.totals["timed_out"] == 1
    assert stats.totals["cancelled"] == 1


@pytest.mark.asyncio
async def test_completion_rate(session: AsyncSession) -> None:
    """2 completed out of 4 terminal → 0.5."""
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(created_at=now, status=TaskStatus.COMPLETED, completed_at=now),
            _task(created_at=now, status=TaskStatus.COMPLETED, completed_at=now),
            _task(created_at=now, status=TaskStatus.TIMED_OUT),
            _task(created_at=now, status=TaskStatus.CANCELLED),
            # In-progress ones DON'T count as "terminal" for the rate.
            _task(created_at=now, status=TaskStatus.CREATED),
            _task(created_at=now, status=TaskStatus.IN_PROGRESS),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    assert stats.completion_rate == 0.5


@pytest.mark.asyncio
async def test_avg_completion_seconds(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(
                created_at=now - timedelta(minutes=5),
                status=TaskStatus.COMPLETED,
                completed_at=now,  # 300s
            ),
            _task(
                created_at=now - timedelta(minutes=15),
                status=TaskStatus.COMPLETED,
                completed_at=now,  # 900s
            ),
            # non-completed — excluded
            _task(
                created_at=now - timedelta(minutes=1),
                status=TaskStatus.TIMED_OUT,
            ),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    assert stats.avg_completion_seconds == pytest.approx(600.0)


@pytest.mark.asyncio
async def test_by_day_zero_fills_window(session: AsyncSession) -> None:
    """Every day in the window gets an entry — even days with no traffic."""
    now = datetime.now(timezone.utc)
    # Create two tasks, three days apart.
    session.add_all(
        [
            _task(created_at=now, status=TaskStatus.CREATED),
            _task(created_at=now - timedelta(days=3), status=TaskStatus.CREATED),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=7)
    assert len(stats.by_day) == 7
    # Last entry is today; first is today - 6.
    today_str = now.date().isoformat()
    assert stats.by_day[-1].date == today_str
    assert stats.by_day[-1].created == 1
    assert stats.by_day[-4].date == (now.date() - timedelta(days=3)).isoformat()
    assert stats.by_day[-4].created == 1


@pytest.mark.asyncio
async def test_by_day_counts_completions(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(
                created_at=now - timedelta(days=2),
                status=TaskStatus.COMPLETED,
                completed_at=now,
            ),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=7)
    today_entry = next(d for d in stats.by_day if d.date == now.date().isoformat())
    two_days_ago = next(
        d for d in stats.by_day if d.date == (now.date() - timedelta(days=2)).isoformat()
    )
    # Created counted at creation date.
    assert two_days_ago.created == 1
    # Completed counted at completion date (today).
    assert today_entry.completed == 1


@pytest.mark.asyncio
async def test_by_channel_only_counts_completed(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(
                created_at=now,
                status=TaskStatus.COMPLETED,
                completed_at=now,
                channel="dashboard",
            ),
            _task(
                created_at=now,
                status=TaskStatus.COMPLETED,
                completed_at=now,
                channel="slack",
            ),
            _task(
                created_at=now,
                status=TaskStatus.COMPLETED,
                completed_at=now,
                channel="slack",
            ),
            # In-progress with a channel still shouldn't be counted.
            _task(
                created_at=now,
                status=TaskStatus.IN_PROGRESS,
                channel="slack",
            ),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    assert stats.by_channel == {"dashboard": 1, "slack": 2}


@pytest.mark.asyncio
async def test_window_filters_old_rows(session: AsyncSession) -> None:
    """Tasks outside the window shouldn't affect by_day / by_channel.

    They DO still show in `totals` — totals are all-time by design.
    """
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(
                created_at=now - timedelta(days=100),
                status=TaskStatus.COMPLETED,
                completed_at=now - timedelta(days=100),
                channel="slack",
            ),
            _task(created_at=now, status=TaskStatus.CREATED),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    # Old task NOT in by_channel (outside window)
    assert stats.by_channel == {}
    # But visible in totals
    assert stats.totals["completed"] == 1
    assert stats.totals["created"] == 1


@pytest.mark.asyncio
async def test_completion_rate_none_when_no_terminals(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _task(created_at=now, status=TaskStatus.CREATED),
            _task(created_at=now, status=TaskStatus.IN_PROGRESS),
        ]
    )
    await session.commit()

    stats = await get_task_stats(session, window_days=30)
    assert stats.completion_rate is None
