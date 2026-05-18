"""Completion attribution survives a Slack-only completer.

Before this fix, `task.completed_by_email` was the only completer
field — for a Slack-only user (no email column) it stayed null and
the audit log rendered "—" even though we knew exactly who clicked
submit. The dashboard route now also stamps `completed_by_user_id`
from the session cookie, the Slack view_submission stamps the
directory user_id from the slack_user_id lookup, and the API
response surfaces both ids plus a join-derived display name.

These tests pin:

  - The dashboard completion flow stamps user_id alongside email
  - Reading the task back (after completion) returns display name
    and slack_user_id when the completer was a Slack-linked user
  - Reading the task back returns the email path's display name
    as the email itself when the completer has no display_name set
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.db.models import User
from awaithumans.server.services.user_service import create_user
from tests.tasks.test_route_authorization import (  # fixture re-exports
    REVIEWER_EMAIL,
    REVIEWER_PASSWORD,
    _admin_headers,
    _login,
    _make_task,
    client,  # noqa: F401  # re-exported so pytest discovers it as a fixture here
    reviewer_user,  # noqa: F401
)

# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def slack_only_user(operator_user: User) -> User:
    """Seed a Slack-only user — no email, no password. Mirrors the
    "TA" case from production: someone added by Slack handle without
    a dashboard credential. The handoff URL would be the only way
    they reach /api/tasks/{id}/complete from the dashboard."""
    from awaithumans.server.db.connection import get_async_session_factory

    async def _seed() -> User:
        factory = get_async_session_factory()
        async with factory() as session:
            return await create_user(
                session,
                email=None,
                display_name="TA",
                is_operator=False,
                password=None,
                slack_user_id="U_TA_REAL_ID",
                slack_team_id="T_WORKSPACE",
            )

    return asyncio.new_event_loop().run_until_complete(_seed())


# ─── Tests ───────────────────────────────────────────────────────────


def test_dashboard_completion_stamps_user_id(client: TestClient, reviewer_user: User) -> None:  # noqa: F811
    """Reviewer logs in, completes a task assigned to them via the
    dashboard. The follow-up GET shows their email AND user_id
    (so a Slack-only completer would also light up)."""
    task_id = _make_task(client, assigned_to_email=REVIEWER_EMAIL)
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)

    submit = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"response": {"approved": True}},
    )
    assert submit.status_code == 200, submit.text

    # Read it back as the same user — they're allowed.
    resp = client.get(f"/api/tasks/{task_id}")
    body = resp.json()
    assert body["completed_by_email"] == REVIEWER_EMAIL
    assert body["completed_by_user_id"] == reviewer_user.id
    # The seeded reviewer has display_name="Reviewer", so the
    # fallback chain returns that — not the email.
    assert body["completed_by_display_name"] == "Reviewer"


def test_slack_only_completer_renders_display_name(
    client: TestClient,  # noqa: F811
    slack_only_user: User,
) -> None:
    """Stamp `completed_by_user_id` directly via the service (the
    dashboard route can't sign Slack-only users in without the
    handoff endpoint, which has its own coverage). The GET
    response should surface the display_name + slack_user_id so
    the audit log doesn't render "—"."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.task_service import complete_task

    # Make a task assigned to TA via the admin path, then complete
    # it as TA via the service layer.
    body = {
        "task": "Approve transfer",
        "payload": {"amt": 100},
        "payload_schema": {"type": "object"},
        "response_schema": {"type": "object"},
        "timeout_seconds": 900,
        "idempotency_key": "slack-only-completer",
        "assign_to": {"email": REVIEWER_EMAIL},  # any active user
    }
    create = client.post("/api/tasks", json=body, headers=_admin_headers())
    task_id = create.json()["id"]

    async def _complete() -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            await complete_task(
                session,
                task_id=task_id,
                response={"approved": True},
                completed_by_email=None,  # TA has no email
                completed_by_user_id=slack_only_user.id,
                completed_via_channel="slack",
            )

    asyncio.new_event_loop().run_until_complete(_complete())

    # Read back via the admin-bearer header (the simplest path; the
    # dashboard's GET path is separately covered).
    resp = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()

    assert body["completed_by_email"] is None
    assert body["completed_by_user_id"] == slack_only_user.id
    assert body["completed_by_display_name"] == "TA"
    assert body["completed_by_slack_user_id"] == "U_TA_REAL_ID"


def test_completed_by_falls_back_to_slack_id_when_no_display_name(
    client: TestClient,  # noqa: F811
    operator_user: User,
) -> None:
    """A Slack-only user without `display_name` set should render
    as `@<slack_user_id>` — same fallback the user-form picker uses
    so the audit log never bottoms out at the row id."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.task_service import complete_task

    async def _seed_user() -> User:
        factory = get_async_session_factory()
        async with factory() as session:
            return await create_user(
                session,
                email=None,
                display_name=None,
                is_operator=False,
                password=None,
                slack_user_id="U_NO_NAME",
                slack_team_id="T_WORKSPACE",
            )

    user = asyncio.new_event_loop().run_until_complete(_seed_user())

    # Make and complete a task as that user.
    create = client.post(
        "/api/tasks",
        json={
            "task": "x",
            "payload": {},
            "payload_schema": {},
            "response_schema": {},
            "timeout_seconds": 900,
            "idempotency_key": "no-display-name",
        },
        headers=_admin_headers(),
    )
    task_id = create.json()["id"]

    async def _complete() -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            await complete_task(
                session,
                task_id=task_id,
                response={"approved": True},
                completed_by_user_id=user.id,
                completed_via_channel="slack",
            )

    asyncio.new_event_loop().run_until_complete(_complete())

    body = client.get(f"/api/tasks/{task_id}", headers=_admin_headers()).json()
    assert body["completed_by_display_name"] == "@U_NO_NAME"
