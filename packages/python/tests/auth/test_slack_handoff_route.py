"""End-to-end test for `/api/auth/slack-handoff`.

Slack-only users (no email/password in the directory) need a way to
clear the dashboard's login wall when they click "Open in Dashboard"
from a Slack DM. The endpoint accepts a signed URL and exchanges it
for a session cookie + a 303 to the task page.

These tests run the real app with a real DB and the real signing path
— end-to-end — because the security model assumes an attacker controls
all four URL params and we want to be sure the endpoint rejects the
attacker without depending on test-only mocks.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.app import create_app
from awaithumans.server.core.slack_handoff import sign_handoff
from awaithumans.server.db.models import User
from awaithumans.utils.constants import DASHBOARD_SESSION_COOKIE_NAME


@pytest.fixture
def client(operator_user: User) -> Iterator[TestClient]:
    """App + seeded operator-as-stand-in. The handoff doesn't care
    about operator vs reviewer; we use the operator fixture because
    it's already wired and the user just needs to exist + be active.

    `follow_redirects=False` so we can inspect the 303."""
    app = create_app(serve_dashboard=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _far_future() -> int:
    return int(time.time()) + 3600


# ─── Happy path ──────────────────────────────────────────────────────


def test_valid_signature_mints_session_and_redirects(
    client: TestClient, operator_user: User
) -> None:
    task_id = "task_" + "a" * 28
    exp = _far_future()
    sig = sign_handoff(user_id=operator_user.id, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/slack-handoff",
        params={"u": operator_user.id, "t": task_id, "e": exp, "s": sig},
    )

    # 303 → /task?id=<task_id>; cookie set on response.
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/task?id={task_id}"
    assert DASHBOARD_SESSION_COOKIE_NAME in resp.cookies


def test_minted_session_is_valid_for_subsequent_requests(
    client: TestClient, operator_user: User
) -> None:
    """After the handoff sets the cookie, a follow-up /api/auth/me
    must report the same user as authenticated. Otherwise the cookie
    is just decoration."""
    task_id = "task_" + "b" * 28
    exp = _far_future()
    sig = sign_handoff(user_id=operator_user.id, task_id=task_id, exp_unix=exp)

    handoff = client.get(
        "/api/auth/slack-handoff",
        params={"u": operator_user.id, "t": task_id, "e": exp, "s": sig},
    )
    assert handoff.status_code == 303

    # The TestClient retains cookies set by previous responses, so
    # the next call carries the session cookie.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user_id"] == operator_user.id


# ─── Reject paths ────────────────────────────────────────────────────


def test_bad_signature_rejected(client: TestClient, operator_user: User) -> None:
    task_id = "task_" + "c" * 28
    exp = _far_future()
    # Sign for a DIFFERENT user so the signature is valid-shaped but
    # the verifier's HMAC compare fails.
    sig = sign_handoff(user_id="someone-else", task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/slack-handoff",
        params={"u": operator_user.id, "t": task_id, "e": exp, "s": sig},
    )
    assert resp.status_code == 400
    assert DASHBOARD_SESSION_COOKIE_NAME not in resp.cookies


def test_expired_link_rejected(client: TestClient, operator_user: User) -> None:
    """A URL with `e=` in the past is rejected even when the
    signature itself is valid for that exp."""
    task_id = "task_" + "d" * 28
    expired = int(time.time()) - 1
    sig = sign_handoff(user_id=operator_user.id, task_id=task_id, exp_unix=expired)

    resp = client.get(
        "/api/auth/slack-handoff",
        params={
            "u": operator_user.id,
            "t": task_id,
            "e": expired,
            "s": sig,
        },
    )
    assert resp.status_code == 400


def test_unknown_user_rejected(client: TestClient) -> None:
    """If the user_id was deleted between sign and click, refuse to
    mint a session for the dead row. The endpoint has to do this
    DB lookup since we don't bake activeness into the URL."""
    task_id = "task_" + "e" * 28
    fake_user = "f" * 32
    exp = _far_future()
    sig = sign_handoff(user_id=fake_user, task_id=task_id, exp_unix=exp)

    resp = client.get(
        "/api/auth/slack-handoff",
        params={"u": fake_user, "t": task_id, "e": exp, "s": sig},
    )
    assert resp.status_code == 403


def test_missing_signature_rejected(client: TestClient, operator_user: User) -> None:
    """Anyone hitting the endpoint with no signature gets 422 from
    FastAPI's required-query-param validation, not 200."""
    resp = client.get(
        "/api/auth/slack-handoff",
        params={
            "u": operator_user.id,
            "t": "task_x",
            "e": _far_future(),
        },  # no `s`
    )
    assert resp.status_code == 422
