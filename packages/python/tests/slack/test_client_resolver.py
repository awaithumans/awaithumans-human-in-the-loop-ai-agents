"""Slack client resolver — single-workspace env token vs OAuth installations."""

from __future__ import annotations

import pytest

from awaithumans.server.channels.slack.client import (
    get_client_for_team,
    get_default_client,
    get_env_client,
)
from awaithumans.server.core.config import settings
from awaithumans.server.services.slack_installation_service import upsert_installation


@pytest.fixture
def reset_env_token():
    """Save/restore SLACK_BOT_TOKEN across a test."""
    original = settings.SLACK_BOT_TOKEN
    yield
    settings.SLACK_BOT_TOKEN = original


def test_env_client_none_when_unset(reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = None
    assert get_env_client() is None


def test_env_client_built_when_set(reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = "xoxb-test"
    client = get_env_client()
    assert client is not None
    # AsyncWebClient stores token on the instance.
    assert client.token == "xoxb-test"


@pytest.mark.asyncio
async def test_client_for_team_uses_stored_installation(session, reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = None  # force installation lookup
    await upsert_installation(
        session,
        team_id="T123",
        team_name="Acme",
        bot_token="xoxb-stored",
        bot_user_id="U_BOT",
        scopes="chat:write",
    )
    client = await get_client_for_team(session, "T123")
    assert client is not None
    assert client.token == "xoxb-stored"


@pytest.mark.asyncio
async def test_client_for_team_falls_back_to_env_when_missing(session, reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = "xoxb-env-fallback"
    client = await get_client_for_team(session, "T_UNKNOWN")
    assert client is not None
    assert client.token == "xoxb-env-fallback"


@pytest.mark.asyncio
async def test_client_for_team_none_when_no_install_and_no_env(session, reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = None
    assert await get_client_for_team(session, "T_UNKNOWN") is None


@pytest.mark.asyncio
async def test_default_client_env_token_wins(session, reset_env_token) -> None:
    """SLACK_BOT_TOKEN set → use it, regardless of installations."""
    settings.SLACK_BOT_TOKEN = "xoxb-env"
    await upsert_installation(
        session,
        team_id="T1",
        team_name="A",
        bot_token="xoxb-stored",
        bot_user_id="U1",
        scopes="chat:write",
    )
    client = await get_default_client(session)
    assert client is not None
    assert client.token == "xoxb-env"


@pytest.mark.asyncio
async def test_default_client_single_installation(session, reset_env_token) -> None:
    """No env token, exactly one install → use it."""
    settings.SLACK_BOT_TOKEN = None
    await upsert_installation(
        session,
        team_id="T1",
        team_name="A",
        bot_token="xoxb-only",
        bot_user_id="U1",
        scopes="chat:write",
    )
    client = await get_default_client(session)
    assert client is not None
    assert client.token == "xoxb-only"


@pytest.mark.asyncio
async def test_default_client_ambiguous_multiple(session, reset_env_token) -> None:
    """No env token, 2+ installations, no team_id → None (ambiguous)."""
    settings.SLACK_BOT_TOKEN = None
    for team_id, token in [("T1", "xoxb-a"), ("T2", "xoxb-b")]:
        await upsert_installation(
            session,
            team_id=team_id,
            team_name=team_id,
            bot_token=token,
            bot_user_id="U",
            scopes="chat:write",
        )
    client = await get_default_client(session)
    assert client is None


@pytest.mark.asyncio
async def test_default_client_no_installations_no_token(session, reset_env_token) -> None:
    settings.SLACK_BOT_TOKEN = None
    assert await get_default_client(session) is None
