"""End-to-end test for `/api/auth/email-handoff`.

The renderer signs the "Review in dashboard" link in every notification
email; the endpoint exchanges that signature for a session cookie. If
the recipient isn't a directory user yet, it auto-provisions a
passwordless reviewer (the agent's `notify=` is implicit consent).

Tests run the real app + real DB so we catch wire-format and route-
gating regressions, not just the signing layer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from awaithumans.server.app import create_app
from awaithumans.server.core.email_handoff import sign_handoff
from awaithumans.server.db.models import User
from awaithumans.utils.constants import DASHBOARD_SESSION_COOKIE_NAME


@pytest.fixture
def client(operator_user: User) -> Iterator[TestClient]:
    """Fresh app + DB. `follow_redirects=False` so we can inspect 303s."""
    app = create_app(serve_dashboard=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _far_future() -> int:
    return int(time.time()) + 3600


# ─── Happy path — known user ─────────────────────────────────────────


def test_valid_signature_for_existing_user_signs_in(
    client: TestClient, operator_user: User
) -> None:
    task_id = "task_existing_user"
    exp = _far_future()
    sig = sign_handoff(recipient=operator_user.email, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/email-handoff",
        params={"to": operator_user.email, "t": task_id, "e": exp, "s": sig},
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/task?id={task_id}"
    assert DASHBOARD_SESSION_COOKIE_NAME in resp.cookies

    # The minted session is valid for /me.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user_id"] == operator_user.id


# ─── Happy path — auto-provision unknown user ────────────────────────


def test_unknown_email_is_auto_provisioned_as_reviewer(
    client: TestClient, operator_user: User
) -> None:
    """The agent's `notify=` is implicit consent to provision. First
    click from an unknown address creates a passwordless reviewer
    and signs them in. They show up in the directory afterward."""
    new_email = "fresh-reviewer@example.com"
    task_id = "task_provision"
    exp = _far_future()
    sig = sign_handoff(recipient=new_email, task_id=task_id, exp_unix=exp)

    # Confirm the user doesn't exist yet.
    from awaithumans.server.db.connection import get_async_session_factory

    async def _exists(email: str) -> bool:
        factory = get_async_session_factory()
        async with factory() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none() is not None

    assert asyncio.new_event_loop().run_until_complete(_exists(new_email)) is False

    resp = client.get(
        "/api/auth/email-handoff",
        params={"to": new_email, "t": task_id, "e": exp, "s": sig},
    )

    assert resp.status_code == 303
    assert DASHBOARD_SESSION_COOKIE_NAME in resp.cookies

    # User now exists, is active, is NOT an operator (auto-provisioned
    # users get reviewer-only privileges), has no password.
    async def _fetch(email: str) -> User | None:
        factory = get_async_session_factory()
        async with factory() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none()

    user = asyncio.new_event_loop().run_until_complete(_fetch(new_email))
    assert user is not None
    assert user.active is True
    assert user.is_operator is False
    assert user.password_hash is None


def test_handoff_uses_existing_user_on_repeat_clicks(
    client: TestClient,
) -> None:
    """First click provisions; second click finds the user and signs
    them in without duplicate inserts (which would 500 on the unique
    email constraint)."""
    new_email = "repeat-clicker@example.com"
    task_id = "task_repeat"
    exp = _far_future()
    sig = sign_handoff(recipient=new_email, task_id=task_id, exp_unix=exp)

    a = client.get(
        "/api/auth/email-handoff",
        params={"to": new_email, "t": task_id, "e": exp, "s": sig},
    )
    assert a.status_code == 303

    # Drop the cookie so the second call goes through the same
    # provision-or-lookup branch, not "already-signed-in".
    client.cookies.clear()

    b = client.get(
        "/api/auth/email-handoff",
        params={"to": new_email, "t": task_id, "e": exp, "s": sig},
    )
    assert b.status_code == 303
    assert DASHBOARD_SESSION_COOKIE_NAME in b.cookies


# ─── Reject paths ────────────────────────────────────────────────────


def test_bad_signature_rejected(client: TestClient) -> None:
    task_id = "task_bad_sig"
    exp = _far_future()
    sig = sign_handoff(
        recipient="someone-else@example.com",
        task_id=task_id,
        exp_unix=exp,
    )

    resp = client.get(
        "/api/auth/email-handoff",
        params={
            "to": "victim@example.com",
            "t": task_id,
            "e": exp,
            "s": sig,
        },
    )
    assert resp.status_code == 400
    assert DASHBOARD_SESSION_COOKIE_NAME not in resp.cookies


def test_expired_link_rejected(client: TestClient) -> None:
    task_id = "task_expired"
    expired = int(time.time()) - 1
    sig = sign_handoff(
        recipient="alice@example.com",
        task_id=task_id,
        exp_unix=expired,
    )

    resp = client.get(
        "/api/auth/email-handoff",
        params={
            "to": "alice@example.com",
            "t": task_id,
            "e": expired,
            "s": sig,
        },
    )
    assert resp.status_code == 400


def test_inactive_existing_user_rejected(client: TestClient) -> None:
    """An operator may have deliberately deactivated a reviewer.
    The handoff doesn't auto-reactivate — same behavior as the
    Slack handoff (#46)."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.user_service import (
        create_user,
        update_user,
    )

    email = "deactivated@example.com"

    async def _seed_inactive() -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            user = await create_user(
                session,
                email=email,
                display_name=None,
                is_operator=False,
                password=None,
            )
            await update_user(session, user_id=user.id, active=False)

    asyncio.new_event_loop().run_until_complete(_seed_inactive())

    task_id = "task_inactive"
    exp = _far_future()
    sig = sign_handoff(recipient=email, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/email-handoff",
        params={"to": email, "t": task_id, "e": exp, "s": sig},
    )
    assert resp.status_code == 403


def test_missing_signature_rejected(client: TestClient) -> None:
    """No `s` param → FastAPI's required-query-param validation 422s
    before we touch the verifier."""
    resp = client.get(
        "/api/auth/email-handoff",
        params={
            "to": "alice@example.com",
            "t": "task_x",
            "e": _far_future(),
        },
    )
    assert resp.status_code == 422


# ─── Handoff-claim path ──────────────────────────────────────────────


def test_handoff_claims_unassigned_task_for_recipient(
    client: TestClient,
) -> None:
    """An auto-provisioned reviewer who clicks the handoff URL must
    be able to actually READ the task they were sent. Without
    handoff-claim, the recipient lands signed-in but with no perms
    (not operator, not assignee → 403 from `require_task_read`).

    The fix: when the task has no assignee yet, the handoff route
    sets `assigned_to_user_id` to the recipient. Same intent the
    Slack DM flow encodes via implicit-assignee derivation at
    create time."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.task_service import (
        create_task,
        get_task,
    )

    new_email = "claim-recipient@example.com"

    async def _make_task() -> str:
        factory = get_async_session_factory()
        async with factory() as session:
            task, _ = await create_task(
                session,
                task="Approve refund",
                payload={},
                payload_schema={},
                response_schema={},
                timeout_seconds=3600,
                idempotency_key="claim-test-1",
            )
            return task.id

    task_id = asyncio.new_event_loop().run_until_complete(_make_task())

    exp = _far_future()
    sig = sign_handoff(recipient=new_email, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/email-handoff",
        params={"to": new_email, "t": task_id, "e": exp, "s": sig},
    )
    assert resp.status_code == 303

    # Task is now assigned to the auto-provisioned reviewer.
    async def _read() -> tuple[str | None, str | None]:
        factory = get_async_session_factory()
        async with factory() as session:
            task = await get_task(session, task_id)
            return task.assigned_to_user_id, task.assigned_to_email

    user_id, email = asyncio.new_event_loop().run_until_complete(_read())
    assert email == new_email
    assert user_id is not None  # the just-provisioned user


def test_handoff_does_not_steal_existing_assignee(client: TestClient, operator_user: User) -> None:
    """If the task already has an assignee, the handoff signs the
    user in (so they can read the task if they're an operator) but
    DOESN'T overwrite the assignee. First-writer-wins protects
    against a second recipient stealing the queue position from
    the first."""
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.task_service import (
        create_task,
        get_task,
    )

    async def _make_assigned_task() -> str:
        factory = get_async_session_factory()
        async with factory() as session:
            task, _ = await create_task(
                session,
                task="Approve",
                payload={},
                payload_schema={},
                response_schema={},
                timeout_seconds=3600,
                idempotency_key="claim-test-2",
                assign_to={"email": operator_user.email},  # pre-assigned
            )
            return task.id

    task_id = asyncio.new_event_loop().run_until_complete(_make_assigned_task())

    # Sign for a different recipient than the assignee.
    other_email = "later-clicker@example.com"
    exp = _far_future()
    sig = sign_handoff(recipient=other_email, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/email-handoff",
        params={"to": other_email, "t": task_id, "e": exp, "s": sig},
    )
    assert resp.status_code == 303  # session minted

    async def _read() -> str | None:
        factory = get_async_session_factory()
        async with factory() as session:
            task = await get_task(session, task_id)
            return task.assigned_to_user_id

    user_id = asyncio.new_event_loop().run_until_complete(_read())
    assert user_id == operator_user.id  # original assignee untouched


# ─── Friendly HTML on failure ─────────────────────────────────────────


def test_expired_link_returns_html_not_json(client: TestClient) -> None:
    """A non-developer clicking a stale email link in their browser must
    not see raw FastAPI JSON. Recipients aren't API consumers; the
    handoff route is browser-only. Returns a brand-styled HTML page
    instead, carrying the same human-readable message."""
    expired = int(time.time()) - 1
    sig = sign_handoff(
        recipient="alice@example.com",
        task_id="task_expired_html",
        exp_unix=expired,
    )

    resp = client.get(
        "/api/auth/email-handoff",
        params={
            "to": "alice@example.com",
            "t": "task_expired_html",
            "e": expired,
            "s": sig,
        },
    )

    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("text/html"), (
        f"Expected HTML response, got {resp.headers.get('content-type')}"
    )
    body = resp.text
    assert "Sign-in link expired" in body
    assert "notification email" in body  # the actionable guidance
    assert "<html" in body.lower()  # actually HTML, not stringified JSON
