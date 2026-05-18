"""Slack target resolution — turn `notify=` strings into IDs Slack accepts.

Public surface: `resolve_slack_target(client, target, team_id)` returns
the user / channel ID that `chat.postMessage` and friends will accept,
or None if resolution failed.

Resolution paths:

  - Channel sigil (`#general`, raw `C…` / `G…` IDs) → pass through.
  - User IDs (`@U…`, `U…`) → strip the `@` and pass through.
  - Email-shaped target → `users.lookupByEmail`.
  - Anything else after `@` → handle lookup via `users.list`,
    matching case-insensitively against `name`,
    `profile.display_name`, and `profile.real_name`.

The handle index is cached per-team for the process lifetime — Slack
rosters change rarely and `users.list` is bulk-rate-limited. Process
restart clears the cache, which is the right invalidation cadence
(staff changes are infrequent enough that "restart to refresh" is
acceptable; Slack quotas matter more).

Used by:
  - `channels/slack/notifier.py` — to translate `notify=` targets
    before posting.
  - `services/task_router.py` — to derive an implicit assignee when
    `notify=` is a single Slack DM target and `assign_to` is None.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger("awaithumans.server.channels.slack.resolution")

# Slack user IDs are `U…` (regular) or `W…` (Enterprise Grid).
_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{5,}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Per-team cache of (handle_lower → user_id). Populated lazily on
# first miss. Process-lifetime — Slack rosters change rarely.
_HANDLE_CACHE: dict[str, dict[str, str]] = {}


def _cache_key_for(team_id: str | None) -> str:
    return team_id or "__default__"


def clear_handle_cache(team_id: str | None = None) -> None:
    """Wipe the cache for one team (or all teams if team_id is None).

    Used by tests; in production the cache is process-lifetime."""
    if team_id is None:
        _HANDLE_CACHE.clear()
        return
    _HANDLE_CACHE.pop(_cache_key_for(team_id), None)


async def resolve_slack_target(
    *,
    client: AsyncWebClient,
    target: str,
    team_id: str | None,
) -> str | None:
    """Resolve a notify/route target to something `chat.postMessage` accepts.

    Returns None on miss — the caller logs and skips. Channel sigils
    pass through unchanged; user IDs are stripped of the leading `@`.
    """
    # Channel broadcast — no resolution needed.
    if target.startswith("#"):
        return target

    body = target.removeprefix("@")

    # Already a real user ID? (`U…` / `W…` followed by alnum)
    if _USER_ID_RE.match(body):
        return body

    # Raw channel-ID shape — public, private, or group DM.
    if body.startswith(("C", "G")) and body[1:].isalnum():
        return body

    # Email shape — Slack has a dedicated lookup endpoint.
    if _EMAIL_RE.match(body):
        return await _resolve_by_email(client=client, email=body, team_id=team_id)

    # Falls through to handle lookup via users.list.
    return await _resolve_by_handle(client=client, handle=body, team_id=team_id)


async def _resolve_by_email(
    *, client: AsyncWebClient, email: str, team_id: str | None
) -> str | None:
    """Use Slack's `users.lookupByEmail` for email-shaped targets."""
    try:
        from slack_sdk.errors import SlackApiError
    except ImportError:
        logger.error("slack_sdk not installed; can't resolve %s", email)
        return None

    try:
        resp = await client.users_lookupByEmail(email=email)
    except SlackApiError as exc:
        logger.warning(
            "users.lookupByEmail failed for %s (team=%s): %s",
            email,
            team_id,
            exc.response.get("error", exc),
        )
        return None

    user = resp.get("user") or {}
    user_id = user.get("id")
    return user_id if isinstance(user_id, str) else None


async def _resolve_by_handle(
    *, client: AsyncWebClient, handle: str, team_id: str | None
) -> str | None:
    """Resolve a Slack handle (e.g. `alice`, `alice.singh`) to a user_id.

    Compares case-insensitively against `name`, `profile.display_name`,
    and `profile.real_name` so any of the three name fields works."""
    cache_key = _cache_key_for(team_id)
    cached = _HANDLE_CACHE.get(cache_key)

    if cached is None:
        cached = await _build_handle_index(client=client, cache_key=cache_key)
        if cached is None:
            return None  # API call failed; logged in helper

    needle = handle.lstrip("@").lower()
    return cached.get(needle)


async def _build_handle_index(*, client: AsyncWebClient, cache_key: str) -> dict[str, str] | None:
    """Walk `users.list` once, build a {handle_lower: user_id} map.

    Skips bots, deleted users, and the Slackbot pseudo-user. Indexes
    each member by every name field they have."""
    try:
        from slack_sdk.errors import SlackApiError
    except ImportError:
        logger.error("slack_sdk not installed; handle resolution unavailable")
        return None

    try:
        resp = await client.users_list()
    except SlackApiError as exc:
        logger.warning(
            "users.list failed for team=%s: %s",
            cache_key,
            exc.response.get("error", exc),
        )
        return None

    index: dict[str, str] = {}
    for m in resp.get("members", []) or []:
        if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
            continue
        user_id = m.get("id")
        if not isinstance(user_id, str):
            continue
        for raw in (
            m.get("name"),
            (m.get("profile") or {}).get("display_name"),
            (m.get("profile") or {}).get("real_name"),
        ):
            if isinstance(raw, str) and raw:
                index[raw.lower()] = user_id

    _HANDLE_CACHE[cache_key] = index
    logger.info(
        "Indexed %d Slack handles for team=%s (cached for process lifetime)",
        len(index),
        cache_key,
    )
    return index
