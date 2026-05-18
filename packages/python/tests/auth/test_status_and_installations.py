"""Status + slack installations routes — response shape + auth + secret leakage."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.app import create_app
from awaithumans.server.services.slack_installation_service import upsert_installation


@pytest.fixture
def client(operator_user) -> Iterator[TestClient]:
    from .conftest import OPERATOR_EMAIL, OPERATOR_PASSWORD

    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        # Log in once so subsequent calls ride the cookie.
        c.post(
            "/api/auth/login",
            json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD},
        )
        yield c


# ─── /api/status ────────────────────────────────────────────────────────


def test_status_shape(client: TestClient) -> None:
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    # Presence check; exact values depend on settings snapshot.
    assert set(data.keys()) == {
        "version",
        "environment",
        "public_url",
        "auth_enabled",
        "payload_encryption_enabled",
        "admin_token_enabled",
        "slack_mode",
        "email_transport",
        "email_from",
    }


def test_status_never_leaks_secrets(client: TestClient) -> None:
    """Config values like PAYLOAD_KEY, DASHBOARD_PASSWORD, SLACK_BOT_TOKEN
    must never appear in the /status response, only boolean presence."""
    resp = client.get("/api/status")
    body = resp.text.lower()
    # These are the env-var names — none should leak.
    for forbidden in (
        "payload_key",
        "dashboard_password",
        "slack_bot_token",
        "slack_client_secret",
        "admin_api_token",
        "smtp_password",
        "resend_key",
    ):
        assert forbidden not in body, f"/status leaked {forbidden}"


def test_status_auth_required(operator_user) -> None:
    """Without a session, /status returns 401 (middleware gate)."""
    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        resp = c.get("/api/status")
        assert resp.status_code == 401


def test_status_slack_mode_detects_single_workspace(client: TestClient, monkeypatch) -> None:
    from awaithumans.server.core.config import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-static")
    resp = client.get("/api/status")
    assert resp.json()["slack_mode"] == "single-workspace"


def test_status_slack_mode_detects_multi_workspace(client: TestClient, monkeypatch) -> None:
    from awaithumans.server.core.config import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "SLACK_CLIENT_ID", "A123")
    monkeypatch.setattr(settings, "SLACK_CLIENT_SECRET", "sekret")
    resp = client.get("/api/status")
    assert resp.json()["slack_mode"] == "multi-workspace"


def test_status_slack_mode_off_when_unconfigured(client: TestClient, monkeypatch) -> None:
    from awaithumans.server.core.config import settings

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "SLACK_CLIENT_ID", None)
    resp = client.get("/api/status")
    assert resp.json()["slack_mode"] == "off"


# ─── /api/channels/slack/installations ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_installations_returns_public_shape(client: TestClient, operator_user) -> None:
    """Seed one installation directly and verify bot_token is never in the response."""
    # Use a fresh session to write the fixture — the TestClient's session
    # runs in a request context and isn't reachable from here.
    from awaithumans.server.db.connection import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        await upsert_installation(
            session,
            team_id="T123",
            team_name="Acme",
            bot_token="xoxb-PLAINTEXT-SECRET",
            bot_user_id="U_BOT",
            scopes="chat:write,im:write",
        )

    resp = client.get("/api/channels/slack/installations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["team_id"] == "T123"
    assert entry["team_name"] == "Acme"
    assert entry["scopes"] == "chat:write,im:write"
    # CRITICAL: the raw token must never appear in the public response.
    assert "bot_token" not in entry
    assert "xoxb" not in resp.text


def test_list_installations_requires_auth(operator_user) -> None:
    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        resp = c.get("/api/channels/slack/installations")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_uninstall_removes_row(client: TestClient, operator_user) -> None:
    from awaithumans.server.db.connection import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        await upsert_installation(
            session,
            team_id="T_REMOVE_ME",
            team_name="GoneSoon",
            bot_token="xoxb-x",
            bot_user_id="U",
            scopes="chat:write",
        )

    resp = client.delete("/api/channels/slack/installations/T_REMOVE_ME")
    assert resp.status_code == 204

    # Subsequent list call must not include it.
    listing = client.get("/api/channels/slack/installations").json()
    ids = [r["team_id"] for r in listing]
    assert "T_REMOVE_ME" not in ids


def test_uninstall_unknown_team_404(client: TestClient) -> None:
    resp = client.delete("/api/channels/slack/installations/T_DOES_NOT_EXIST")
    assert resp.status_code == 404
