"""Slack AsyncWebClient resolution.

Three ways to end up with a client, in priority order:

1. `get_client_for_team(session, team_id)` — use the OAuth installation
   stored in `slack_installations` for that workspace. This is what the
   interactivity webhook uses, since every incoming payload carries
   `team.id`.
2. `get_default_client(session)` — for outbound notifications where the
   caller doesn't know which workspace. Resolution:
     - If AWAITHUMANS_SLACK_BOT_TOKEN is set → use it (single-workspace
       self-hosted mode).
     - Else, if exactly one installation exists → use it.
     - Else → None (ambiguous; notifier logs and skips).
3. `get_env_client()` — synchronous fallback for callers without a DB
   session. Only returns a client when SLACK_BOT_TOKEN is set.

The thin wrapper lets the rest of the server stay free of slack_sdk
imports and lets tests swap in a fake client without touching every
call site.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.config import settings
from awaithumans.server.services.slack_installation_service import (
    get_installation,
    list_installations,
)

if TYPE_CHECKING:  # pragma: no cover
    from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger("awaithumans.server.channels.slack.client")


def _build_client(token: str) -> AsyncWebClient | None:
    try:
        from slack_sdk.web.async_client import AsyncWebClient
    except ImportError:
        logger.error('slack_sdk not installed. Install with: pip install "awaithumans[server]"')
        return None
    return AsyncWebClient(token=token)


def get_env_client() -> AsyncWebClient | None:
    """Return a client built from SLACK_BOT_TOKEN, or None if unset."""
    if not settings.SLACK_BOT_TOKEN:
        return None
    return _build_client(settings.SLACK_BOT_TOKEN)


async def get_client_for_team(session: AsyncSession, team_id: str | None) -> AsyncWebClient | None:
    """Return the client for a specific Slack workspace.

    Order: installation for team_id → env token fallback → None.
    """
    if team_id:
        install = await get_installation(session, team_id)
        if install is not None:
            return _build_client(install.bot_token)
        logger.warning(
            "No installation found for team_id=%s; falling back to env token.",
            team_id,
        )
    return get_env_client()


async def get_default_client(session: AsyncSession) -> AsyncWebClient | None:
    """Return a client for outbound use when no team_id is known.

    Used by notify_task() where the task's routing strings don't name a
    workspace. In self-hosted single-workspace setups, env token wins.
    In distributed setups with exactly one OAuth installation, that one
    wins. Ambiguous case (multiple installations, no env token) → None.
    """
    if settings.SLACK_BOT_TOKEN:
        return _build_client(settings.SLACK_BOT_TOKEN)

    installs = await list_installations(session)
    if len(installs) == 1:
        return _build_client(installs[0].bot_token)

    if len(installs) > 1:
        logger.warning(
            "Multiple Slack installations exist (%d) but task has no team_id; "
            "cannot pick a workspace. Add a team_id-specific notify entry.",
            len(installs),
        )
    return None
