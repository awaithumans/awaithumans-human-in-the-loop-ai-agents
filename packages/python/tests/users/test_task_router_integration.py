"""create_task + router integration — verifies the task row reflects
what the router decided, and fairness state advances across calls."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.services.task_service import create_task
from awaithumans.server.services.user_service import create_user


async def _create(session: AsyncSession, idempotency_key: str, **kwargs) -> object:
    task, _ = await create_task(
        session,
        task="review refund",
        payload={},
        payload_schema={},
        response_schema={},
        timeout_seconds=60,
        idempotency_key=idempotency_key,
        **kwargs,
    )
    return task


@pytest.mark.asyncio
async def test_create_task_sets_assigned_to_email_from_router(
    session: AsyncSession,
) -> None:
    u = await create_user(session, email="alice@x.com", role="kyc")

    task = await _create(
        session,
        idempotency_key="task-a",
        assign_to={"role": "kyc"},
    )
    assert task.assigned_to_email == "alice@x.com"
    assert task.assigned_to_user_id == u.id


@pytest.mark.asyncio
async def test_create_task_with_explicit_email(
    session: AsyncSession,
) -> None:
    u = await create_user(session, email="alice@x.com")

    task = await _create(
        session,
        idempotency_key="task-b",
        assign_to={"email": "alice@x.com"},
    )
    assert task.assigned_to_email == "alice@x.com"
    assert task.assigned_to_user_id == u.id


@pytest.mark.asyncio
async def test_create_task_unroutable_stays_unassigned(
    session: AsyncSession,
) -> None:
    """No matching user → task is reachable but unassigned. Operator
    can pick it up from the dashboard; nothing silently fails."""
    task = await _create(
        session,
        idempotency_key="task-c",
        assign_to={"role": "kyc-reviewer"},
    )
    assert task.assigned_to_email is None
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_create_task_null_assign_to_ok(session: AsyncSession) -> None:
    """A task created with no `assign_to` just enters the queue."""
    task = await _create(session, idempotency_key="task-d")
    assert task.assigned_to_email is None
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_fairness_across_two_tasks(session: AsyncSession) -> None:
    """Two tasks routed to the same filter must land on different
    users when both are eligible."""
    a = await create_user(session, email="a@x.com", role="kyc")
    b = await create_user(session, email="b@x.com", role="kyc")

    t1 = await _create(session, idempotency_key="t1", assign_to={"role": "kyc"})
    t2 = await _create(session, idempotency_key="t2", assign_to={"role": "kyc"})

    assigned = {t1.assigned_to_user_id, t2.assigned_to_user_id}
    assert assigned == {a.id, b.id}


@pytest.mark.asyncio
async def test_slack_only_user_assigned_with_null_email(
    session: AsyncSession,
) -> None:
    """Slack-only users get tasks assigned to them — `assigned_to_email`
    stays null but `assigned_to_user_id` points at them."""
    u = await create_user(session, slack_team_id="T01", slack_user_id="U01", role="kyc")

    task = await _create(session, idempotency_key="slack-task", assign_to={"role": "kyc"})
    assert task.assigned_to_email is None
    assert task.assigned_to_user_id == u.id


@pytest.mark.asyncio
async def test_idempotency_key_dedups_before_routing(
    session: AsyncSession,
) -> None:
    """A duplicate idempotency_key returns the existing task — router
    must NOT run twice and advance the user's counter unfairly."""
    a = await create_user(session, email="a@x.com", role="kyc")

    t1 = await _create(session, idempotency_key="shared", assign_to={"role": "kyc"})
    assert t1.assigned_to_user_id == a.id

    # Capture the user's last_assigned_at after the first task.
    await session.refresh(a)
    ts_after_first = a.last_assigned_at
    assert ts_after_first is not None

    # Second create with same idempotency key — should return the
    # same task without bumping the user's timestamp.
    t2 = await _create(session, idempotency_key="shared", assign_to={"role": "kyc"})
    assert t2.id == t1.id

    await session.refresh(a)
    assert a.last_assigned_at == ts_after_first
