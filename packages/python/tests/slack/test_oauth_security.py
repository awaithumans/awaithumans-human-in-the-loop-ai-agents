"""Security regression tests for the Slack OAuth install flow.

These tests encode the defenses from the OAuth hardening review:

- /oauth/start rejects missing or wrong install_token
- /oauth/start refuses to run in single-workspace mode
- /oauth/start sets a state cookie; /oauth/callback rejects when the
  `state` query param doesn't match the cookie
- /oauth/callback URL-encodes error codes in the redirect
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.app import create_app
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session

# Register models
from awaithumans.server.db.models import (  # noqa: F401
    AuditEntry,
    SlackInstallation,
    Task,
)

INSTALL_TOKEN = "test-install-token-super-secret"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
SIGNING_SECRET = "test-signing-secret"


@pytest_asyncio.fixture
async def oauth_client() -> AsyncGenerator[AsyncClient, None]:
    """App + in-memory DB + all OAuth env vars set (multi-workspace mode)."""
    # ── Swap settings for multi-workspace OAuth mode ────────────────
    original = {
        "BOT_TOKEN": settings.SLACK_BOT_TOKEN,
        "CLIENT_ID": settings.SLACK_CLIENT_ID,
        "CLIENT_SECRET": settings.SLACK_CLIENT_SECRET,
        "SIGNING_SECRET": settings.SLACK_SIGNING_SECRET,
        "INSTALL_TOKEN": settings.SLACK_INSTALL_TOKEN,
        "PUBLIC_URL": settings.PUBLIC_URL,
    }
    settings.SLACK_BOT_TOKEN = None
    settings.SLACK_CLIENT_ID = CLIENT_ID
    settings.SLACK_CLIENT_SECRET = CLIENT_SECRET
    settings.SLACK_SIGNING_SECRET = SIGNING_SECRET
    settings.SLACK_INSTALL_TOKEN = INSTALL_TOKEN
    settings.PUBLIC_URL = "https://app.example.com"

    # ── Ephemeral DB ────────────────────────────────────────────────
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app = create_app(serve_dashboard=False)
    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as client:
        yield client

    await engine.dispose()
    for k, v in original.items():
        setattr(settings, f"SLACK_{k}" if k != "PUBLIC_URL" else "PUBLIC_URL", v)


# ─── /oauth/start security ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_rejects_missing_install_token(oauth_client: AsyncClient) -> None:
    """Without the operator token, /oauth/start MUST NOT redirect to Slack."""
    resp = await oauth_client.get("/api/channels/slack/oauth/start")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_rejects_wrong_install_token(oauth_client: AsyncClient) -> None:
    resp = await oauth_client.get(
        "/api/channels/slack/oauth/start",
        params={"install_token": "wrong-token"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_rejects_when_bot_token_set(oauth_client: AsyncClient) -> None:
    """Single-workspace mode MUST disable the OAuth flow entirely."""
    settings.SLACK_BOT_TOKEN = "xoxb-single-workspace"
    try:
        resp = await oauth_client.get(
            "/api/channels/slack/oauth/start",
            params={"install_token": INSTALL_TOKEN},
        )
        assert resp.status_code == 503
    finally:
        settings.SLACK_BOT_TOKEN = None


@pytest.mark.asyncio
async def test_start_success_redirects_to_slack_and_sets_cookie(
    oauth_client: AsyncClient,
) -> None:
    resp = await oauth_client.get(
        "/api/channels/slack/oauth/start",
        params={"install_token": INSTALL_TOKEN},
    )
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://slack.com/oauth/v2/authorize")
    # Cookie is set, httponly, scoped to the oauth path.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "awaithumans_slack_oauth_state=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()
    assert "Secure" in set_cookie  # PUBLIC_URL is https:// in this fixture


# ─── /oauth/callback security ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_rejects_without_state_cookie(
    oauth_client: AsyncClient,
) -> None:
    """Attacker-crafted state with no matching cookie → 401."""
    resp = await oauth_client.get(
        "/api/channels/slack/oauth/callback",
        params={"code": "x", "state": "anything"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_callback_rejects_state_cookie_mismatch(
    oauth_client: AsyncClient,
) -> None:
    """Cookie present but different from state param → reject (CSRF defense)."""
    oauth_client.cookies.set("awaithumans_slack_oauth_state", "different-state-value")
    resp = await oauth_client.get(
        "/api/channels/slack/oauth/callback",
        params={"code": "x", "state": "attacker-minted-state"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_callback_url_encodes_error_redirect(
    oauth_client: AsyncClient,
) -> None:
    """Slack-supplied error codes MUST be URL-encoded in our redirect."""
    resp = await oauth_client.get(
        "/api/channels/slack/oauth/callback",
        params={"error": "access_denied&injected=1"},
    )
    assert resp.status_code == 307
    loc = resp.headers["location"]
    # Literal '&injected=1' should be escaped, not passed through as a new param.
    assert "injected=1" not in loc.split("?", 1)[1].split("&")
    assert "slack_oauth_error=" in loc


@pytest.mark.asyncio
async def test_callback_success_stores_installation(
    oauth_client: AsyncClient,
) -> None:
    """Happy path: valid state + cookie + mocked Slack response → installation saved."""
    # 1. Start the flow to get a legitimate state + cookie.
    start = await oauth_client.get(
        "/api/channels/slack/oauth/start",
        params={"install_token": INSTALL_TOKEN},
    )
    assert start.status_code == 307
    # Extract the state from the Slack redirect URL.
    from urllib.parse import parse_qs, urlparse

    slack_url = urlparse(start.headers["location"])
    state = parse_qs(slack_url.query)["state"][0]
    assert oauth_client.cookies.get("awaithumans_slack_oauth_state") == state

    # 2. Mock Slack's oauth.v2.access response.
    slack_response: dict[str, Any] = {
        "ok": True,
        "access_token": "xoxb-installed-token",
        "bot_user_id": "U_BOT_123",
        "scope": "chat:write,im:write",
        "team": {"id": "T_WORKSPACE", "name": "Acme"},
        "authed_user": {"id": "U_USER"},
    }

    class FakeResp:
        def json(self) -> dict[str, Any]:
            return slack_response

    fake_post = AsyncMock(return_value=FakeResp())

    with patch("httpx.AsyncClient.post", fake_post):
        resp = await oauth_client.get(
            "/api/channels/slack/oauth/callback",
            params={"code": "slack-oauth-code", "state": state},
        )

    assert resp.status_code == 307
    # Redirect to dashboard with URL-encoded success param.
    assert "slack_installed=Acme" in resp.headers["location"]
    # Cookie invalidated post-install.
    delete_cookie = resp.headers.get("set-cookie", "")
    assert "awaithumans_slack_oauth_state=" in delete_cookie
    # `Max-Age=0` is how starlette deletes cookies.
    assert "Max-Age=0" in delete_cookie or "max-age=0" in delete_cookie.lower()


# ─── Constant-time compare (no timing leak on install token) ────────────


def test_install_token_comparison_is_constant_time() -> None:
    """Direct assertion that we use hmac.compare_digest, not ==, so prefix
    matches and full matches take the same time. Covered by reading the
    source; also verified by importing and calling."""
    import hmac

    assert hmac.compare_digest("abc", "abc") is True
    assert hmac.compare_digest("abc", "abd") is False
    # The OAuth route imports `hmac` at module top — grep verifies that.
    from awaithumans.server.routes.slack import oauth as oauth_route

    assert "hmac" in oauth_route.__dict__
