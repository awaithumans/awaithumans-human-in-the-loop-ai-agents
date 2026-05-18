"""Per-task authorization at the HTTP layer.

These tests exercise `/api/tasks/*` routes through real HTTP, asserting
the auth gates added in PR-sec-1:

  - admin bearer: full access (agent / ops scripts)
  - operator session: full access
  - non-operator session: scoped to their own assigned tasks; cannot
    read or complete other people's tasks; cannot create / cancel /
    audit anything

Without these gates, any logged-in user could enumerate every task,
read full payloads + responses, complete tasks they were never
assigned to, and uninstall Slack workspaces. The reviews surfaced
this as the launch-blocking authorisation gap; tests below pin the
fix so a regression can't slip past CI.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.app import create_app
from awaithumans.server.core.config import settings
from awaithumans.server.db.models import User
from tests.auth.conftest import OPERATOR_EMAIL, OPERATOR_PASSWORD

REVIEWER_EMAIL = "reviewer@example.com"
REVIEWER_PASSWORD = "reviewer-correct-horse-battery"
OTHER_USER_EMAIL = "other@example.com"


@pytest.fixture
def client(operator_user: User) -> Iterator[TestClient]:
    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def reviewer_user(operator_user: User) -> User:
    """Seed a non-operator reviewer alongside the operator. Used to
    assert non-operators get scoped task visibility and 403s on
    operator-only routes."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.user_service import create_user

    async def _seed() -> User:
        factory = get_async_session_factory()
        async with factory() as session:
            return await create_user(
                session,
                email=REVIEWER_EMAIL,
                display_name="Reviewer",
                is_operator=False,
                password=REVIEWER_PASSWORD,
            )

    return asyncio.new_event_loop().run_until_complete(_seed())


@pytest.fixture
def other_user(operator_user: User) -> User:
    """A second non-operator — used as the assignee on tasks the
    reviewer should NOT be able to see."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.user_service import create_user

    async def _seed() -> User:
        factory = get_async_session_factory()
        async with factory() as session:
            return await create_user(
                session,
                email=OTHER_USER_EMAIL,
                display_name="Other",
                is_operator=False,
            )

    return asyncio.new_event_loop().run_until_complete(_seed())


def _admin_headers() -> dict[str, str]:
    settings.ADMIN_API_TOKEN = "test-admin-token"
    return {"Authorization": f"Bearer {settings.ADMIN_API_TOKEN}"}


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 204, resp.text


def _make_task(
    client: TestClient,
    *,
    assigned_to_email: str | None = None,
    idempotency_key: str = "task-1",
) -> str:
    body: dict = {
        "task": "Approve refund",
        "payload": {"amount": 100},
        "payload_schema": {"type": "object"},
        "response_schema": {"type": "object"},
        "timeout_seconds": 900,
        "idempotency_key": idempotency_key,
    }
    if assigned_to_email is not None:
        body["assign_to"] = {"email": assigned_to_email}
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ─── Create / cancel / audit are operator-or-admin only ──────────────────


def test_create_task_blocked_for_non_operator(client: TestClient, reviewer_user: User) -> None:
    """Non-operator session must NOT be able to create tasks. The agent
    is the canonical caller (admin bearer); allowing reviewers to
    create tasks would let them bypass the verifier-config and routing
    decisions baked into agent code."""
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.post(
        "/api/tasks",
        json={
            "task": "x",
            "payload": {},
            "payload_schema": {},
            "response_schema": {},
            "timeout_seconds": 900,
            "idempotency_key": "no",
        },
    )
    assert resp.status_code == 403


def test_cancel_blocked_for_non_operator(
    client: TestClient, reviewer_user: User, other_user: User
) -> None:
    """Even the assignee shouldn't be able to cancel — the agent
    expects to see the task through to completion or timeout."""
    task_id = _make_task(client, assigned_to_email=REVIEWER_EMAIL)
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 403


def test_audit_visible_to_assignee(client: TestClient, reviewer_user: User) -> None:
    """The assignee can read their own task's audit trail.

    The earlier policy was operator-only; that 403'd the dashboard's
    /task page for any non-operator assignee because the page fetches
    audit alongside the task. The audit only quotes data the assignee
    already sees on the task itself (their own response, completer
    email, verifier reasoning), so withholding it just broke the UI
    without protecting anything."""
    task_id = _make_task(client, assigned_to_email=REVIEWER_EMAIL)
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get(f"/api/tasks/{task_id}/audit")
    assert resp.status_code == 200


def test_audit_blocked_for_non_assignee(
    client: TestClient, reviewer_user: User, other_user: User
) -> None:
    """Cross-assignee enumeration is still refused — a logged-in
    reviewer can't read someone else's audit trail."""
    task_id = _make_task(client, assigned_to_email=OTHER_USER_EMAIL)
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get(f"/api/tasks/{task_id}/audit")
    assert resp.status_code == 403


# ─── List is scoped to assignee for non-operators ────────────────────────


def test_list_scoped_to_assignee_for_non_operator(
    client: TestClient, reviewer_user: User, other_user: User
) -> None:
    """A reviewer logged into the dashboard must only see tasks routed
    to them — not the operator's, not other reviewers'."""
    mine = _make_task(client, assigned_to_email=REVIEWER_EMAIL, idempotency_key="mine")
    _theirs = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="theirs")

    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert mine in ids
    assert _theirs not in ids


def test_list_unscoped_for_operator(client: TestClient, reviewer_user: User) -> None:
    """Operators see every task regardless of assignee."""
    a = _make_task(client, assigned_to_email=REVIEWER_EMAIL, idempotency_key="a")
    b = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="b")
    _login(client, "operator@example.com", OPERATOR_PASSWORD)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert a in ids
    assert b in ids


def test_list_assigned_to_filter_ignored_for_non_operator(
    client: TestClient, reviewer_user: User, other_user: User
) -> None:
    """A reviewer can't pass `assigned_to=other@example.com` to read
    someone else's tasks — server forces the filter to their own ID."""
    mine = _make_task(client, assigned_to_email=REVIEWER_EMAIL, idempotency_key="m")
    _theirs = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="t")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get(f"/api/tasks?assigned_to={OTHER_USER_EMAIL}")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert _theirs not in ids
    assert ids == [mine] or ids == []


# ─── Get / complete enforce assignee match for non-operators ─────────────


def test_get_other_users_task_403_for_non_operator(client: TestClient, reviewer_user: User) -> None:
    """Direct ID lookup must also gate — the reviewer might know a
    task ID from a stale Slack message."""
    task_id = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="g")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 403


def test_get_own_task_succeeds_for_assignee(client: TestClient, reviewer_user: User) -> None:
    task_id = _make_task(client, assigned_to_email=REVIEWER_EMAIL, idempotency_key="g2")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id


def test_complete_other_users_task_403_for_non_operator(
    client: TestClient, reviewer_user: User
) -> None:
    """The most consequential gate: a non-operator must NOT complete a
    task assigned to someone else, even with a known task_id."""
    task_id = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="c")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"response": {"approved": True}},
    )
    assert resp.status_code == 403


def test_complete_own_task_succeeds_for_assignee(client: TestClient, reviewer_user: User) -> None:
    task_id = _make_task(client, assigned_to_email=REVIEWER_EMAIL, idempotency_key="c2")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"response": {"approved": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


# ─── /claim ─────────────────────────────────────────────────────────────


def test_claim_assigns_unassigned_task_to_caller(client: TestClient, operator_user: User) -> None:
    """The dashboard happy path: an operator opens an unassigned task
    and clicks Claim. The route should pin them as the assignee so the
    response form renders on the next page load."""
    task_id = _make_task(client, idempotency_key="claim-1")  # no assign_to
    _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = client.post(f"/api/tasks/{task_id}/claim")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_to_email"] == OPERATOR_EMAIL
    assert body["assigned_to_user_id"] == operator_user.id


def test_claim_blocked_for_non_operator(client: TestClient, reviewer_user: User) -> None:
    """Claim is operator-or-admin (mirror of cancel/audit). A
    non-operator reviewer must not be able to claim broadcast tasks
    out from under operators — claim is an operator action."""
    task_id = _make_task(client, idempotency_key="claim-2")
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)
    resp = client.post(f"/api/tasks/{task_id}/claim")
    assert resp.status_code == 403


def test_claim_with_admin_bearer_400(client: TestClient) -> None:
    """Admin bearer is the AI agent's identity — there's no human
    user_id to pin as assignee. The route fails fast with 400 so the
    caller gets a clear "log in as an operator first" message instead
    of a misleading 200 with a null assignee."""
    task_id = _make_task(client, idempotency_key="claim-3")
    resp = client.post(f"/api/tasks/{task_id}/claim", headers=_admin_headers())
    assert resp.status_code == 400
    assert "operator" in resp.json()["detail"].lower()


def test_claim_already_assigned_returns_409(
    client: TestClient, operator_user: User, other_user: User
) -> None:
    """Claim is first-writer-wins via the existing service. Trying to
    claim a task that already has an assignee surfaces
    `TaskAlreadyClaimedError` → 409, so the dashboard can render
    "claimed by Other"."""
    task_id = _make_task(client, assigned_to_email=OTHER_USER_EMAIL, idempotency_key="claim-4")
    _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = client.post(f"/api/tasks/{task_id}/claim")
    assert resp.status_code == 409


def test_claim_terminal_task_returns_409(client: TestClient, operator_user: User) -> None:
    """Claim on a completed/cancelled/timed-out task is meaningless.
    The service raises `TaskAlreadyTerminalError` which the central
    handler maps to 409."""
    task_id = _make_task(
        client,
        assigned_to_email=OPERATOR_EMAIL,
        idempotency_key="claim-5",
    )
    # Operator completes it via admin (so we don't tangle the test
    # with an extra login round-trip).
    client.post(
        f"/api/tasks/{task_id}/complete",
        json={"response": {"approved": True}},
        headers=_admin_headers(),
    )
    _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = client.post(f"/api/tasks/{task_id}/claim")
    assert resp.status_code == 409


# ─── List filter: ?unassigned=true ─────────────────────────────────────


def test_list_unassigned_true_returns_only_unassigned(
    client: TestClient, operator_user: User
) -> None:
    """Operator's "Unassigned" filter on the dashboard must surface
    ONLY tasks where no human owns the row yet. Mixed-state lists hide
    the broadcast queue under regular routed tasks."""
    unassigned_id = _make_task(client, idempotency_key="filter-1")
    assigned_id = _make_task(
        client,
        assigned_to_email=OPERATOR_EMAIL,
        idempotency_key="filter-2",
    )

    resp = client.get("/api/tasks?unassigned=true", headers=_admin_headers())
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert unassigned_id in ids
    assert assigned_id not in ids


def test_list_unassigned_ignored_for_non_operator(client: TestClient, reviewer_user: User) -> None:
    """A non-operator passing `?unassigned=true` would otherwise widen
    their visibility past their own assigned tasks. The route must
    drop the flag for non-operators (server-side scoping wins)."""
    _make_task(client, idempotency_key="filter-3")  # unassigned
    own = _make_task(
        client,
        assigned_to_email=REVIEWER_EMAIL,
        idempotency_key="filter-4",
    )
    _login(client, REVIEWER_EMAIL, REVIEWER_PASSWORD)

    resp = client.get("/api/tasks?unassigned=true")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    # The reviewer should see only their own task — the unassigned
    # broadcast does not leak into their queue.
    assert ids == {own}


# ─── List filter: ?assigned_to=X (broad search across user fields) ─────


def test_list_assigned_to_matches_email_exact(client: TestClient, operator_user: User) -> None:
    """Pre-#73 this was the only working query shape. Pin it so the
    new broader logic doesn't regress the email-exact path."""
    target = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="assignee-1")
    _make_task(client, idempotency_key="assignee-1b")  # noise
    resp = client.get(f"/api/tasks?assigned_to={OPERATOR_EMAIL}", headers=_admin_headers())
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert target in ids


def test_list_assigned_to_matches_display_name_substring(
    client: TestClient, operator_user: User
) -> None:
    """The user's actual ask: typing "operator" (display_name) finds
    the task assigned to ops@example.com. Pre-#73 returned [].
    Substring match is case-insensitive — typing "OP" or "ope" works
    too."""
    target = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="assignee-2")
    # operator_user has display_name="Op Erator" or similar; check
    # the conftest. We assume display_name is set to something that
    # contains "op" (case-insensitive). If it isn't, the conftest
    # needs to be updated alongside this filter.
    resp = client.get("/api/tasks?assigned_to=op", headers=_admin_headers())
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert target in ids, (
        "expected task assigned to operator to surface via display-name "
        f"substring. operator_user.display_name={operator_user.display_name!r}"
    )


def test_list_assigned_to_matches_slack_user_id_exact(
    client: TestClient, operator_user: User
) -> None:
    """Slack-only users have no email but are assignable via
    slack_user_id. The broad filter should match it exactly."""
    # Patch the operator to have a slack_user_id we can search for.
    from awaithumans.server.db.connection import get_async_session_factory

    async def _attach_slack() -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            user = await session.get(User, operator_user.id)
            assert user is not None
            user.slack_user_id = "U_TEST_OP"
            session.add(user)
            await session.commit()

    asyncio.new_event_loop().run_until_complete(_attach_slack())

    target = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="assignee-3")
    resp = client.get("/api/tasks?assigned_to=U_TEST_OP", headers=_admin_headers())
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert target in ids


def test_list_assigned_to_unknown_returns_empty(client: TestClient, operator_user: User) -> None:
    """Search that matches no user AND no assigned_to_email returns
    an empty list, not an error."""
    _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="assignee-4")
    resp = client.get(
        "/api/tasks?assigned_to=nobody-by-this-name-exists",
        headers=_admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ─── List filter: ?terminal=true (audit-log view) ───────────────────────


def test_list_terminal_true_returns_only_terminal(client: TestClient, operator_user: User) -> None:
    """Audit Log uses ?terminal=true to fetch all completed/timed-out/
    cancelled/verification-exhausted tasks in one call. Active tasks
    (status=created etc) must NOT leak in."""
    active = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="term-1")
    completed = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="term-2")
    client.post(
        f"/api/tasks/{completed}/complete",
        json={"response": {"approved": True}},
        headers=_admin_headers(),
    )

    resp = client.get("/api/tasks?terminal=true", headers=_admin_headers())
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert completed in ids
    assert active not in ids


def test_list_terminal_true_with_status_keeps_explicit_status(
    client: TestClient, operator_user: User
) -> None:
    """When both are set, status= wins. terminal=true is the
    "any-terminal" shorthand; if you want a specific terminal status
    you can scope to one (e.g. status=cancelled) and the dashboard
    still works."""
    completed = _make_task(client, assigned_to_email=OPERATOR_EMAIL, idempotency_key="term-3")
    client.post(
        f"/api/tasks/{completed}/complete",
        json={"response": {"approved": True}},
        headers=_admin_headers(),
    )

    resp = client.get(
        "/api/tasks?terminal=true&status=cancelled",
        headers=_admin_headers(),
    )
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    # The completed task is terminal but NOT cancelled, so the
    # status= filter excludes it.
    assert completed not in ids
