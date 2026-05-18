"""Slack installations CRUD — list + uninstall + workspace members.

Public shape never includes `bot_token` (encrypted, stays server-side).
Dashboard Settings page lists these and offers per-row uninstall.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from slack_sdk.errors import SlackApiError
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.channels.slack.client import (
    get_client_for_team,
    get_env_client,
)
from awaithumans.server.core.admin_auth import require_admin
from awaithumans.server.db.connection import get_session
from awaithumans.server.schemas.slack import (
    SlackInstallationResponse,
    SlackMemberResponse,
    SlackStaticWorkspaceResponse,
)
from awaithumans.server.services.slack_installation_service import (
    delete_installation,
    list_installations,
)
from awaithumans.utils.constants import SLACK_TEAM_ID_MAX_LENGTH

# Every route in this module is operator-only — these are
# infrastructure-management surfaces (uninstall a workspace, list
# every member's name + admin status, see which workspace owns the
# static token). Without this gate, any logged-in non-operator could
# silently DoS the Slack integration by hitting DELETE.
router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger("awaithumans.server.routes.slack.installations")

# Cached static-token workspace info. `auth.test` is a network call we
# don't want to repeat on every dashboard mount — the bot token rarely
# changes during a process's lifetime, and the team_id behind it is
# stable. Cleared on process restart, which is the right invalidation
# (env vars only change between restarts anyway).
_static_workspace_cache: SlackStaticWorkspaceResponse | None = None


def _to_public(row) -> SlackInstallationResponse:
    return SlackInstallationResponse(
        team_id=row.team_id,
        team_name=row.team_name,
        bot_user_id=row.bot_user_id,
        scopes=row.scopes,
        enterprise_id=row.enterprise_id,
        installed_by_user_id=row.installed_by_user_id,
        installed_at=row.installed_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.get("/installations", response_model=list[SlackInstallationResponse])
async def list_slack_installations(
    session: AsyncSession = Depends(get_session),
) -> list[SlackInstallationResponse]:
    rows = await list_installations(session)
    return [_to_public(r) for r in rows]


@router.get(
    "/static-workspace",
    response_model=SlackStaticWorkspaceResponse,
)
async def get_static_workspace() -> SlackStaticWorkspaceResponse:
    """Surface the workspace behind `SLACK_BOT_TOKEN` for the dashboard.

    Static-token mode doesn't write to `slack_installations`, so the
    "Slack workspaces" Settings panel had no way to display which
    workspace the integration was connected to. This endpoint calls
    Slack's `auth.test` to fetch team_id + team_name for the env
    token and returns them as a read-only entry.

    404 when not in static-token mode (i.e. `SLACK_BOT_TOKEN` unset)
    so the dashboard can branch on the absence cleanly. 502 if Slack
    rejects the token (revoked, malformed) — same shape as the
    member-list endpoint for consistency.
    """
    global _static_workspace_cache
    if _static_workspace_cache is not None:
        return _static_workspace_cache

    client = get_env_client()
    if client is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No static bot token configured. Set SLACK_BOT_TOKEN to "
                "use single-workspace mode, or install the app via OAuth."
            ),
        )

    try:
        resp = await client.auth_test()
    except SlackApiError as exc:
        # Specific catch — bare `except Exception` would mask network
        # / runtime errors as "token rejected." Slack's own auth
        # failures all surface as SlackApiError; anything else is a
        # genuine bug and should propagate to the central handler.
        logger.warning("Slack auth.test failed for static token: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=(
                "Slack rejected auth.test for the static bot token. "
                "Token may be revoked or malformed — verify "
                "SLACK_BOT_TOKEN and reinstall the app if needed."
            ),
        ) from exc

    data = resp.data  # type: ignore[attr-defined]
    info = SlackStaticWorkspaceResponse(
        team_id=str(data.get("team_id") or ""),
        team_name=data.get("team") or None,
        bot_user_id=data.get("user_id") or data.get("bot_id") or None,
    )
    if not info.team_id:
        raise HTTPException(
            status_code=502,
            detail="Slack auth.test returned no team_id — token shape unexpected.",
        )

    _static_workspace_cache = info
    return info


@router.delete(
    "/installations/{team_id}",
    status_code=204,
    # Override the default JSONResponse — 204 forbids a response body, and
    # FastAPI's default response would try to emit one.
    response_class=Response,
)
async def uninstall_slack_workspace(
    team_id: str = Path(..., min_length=1, max_length=SLACK_TEAM_ID_MAX_LENGTH),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ok = await delete_installation(session, team_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Installation not found.")
    logger.info("Uninstalled Slack workspace team_id=%s", team_id)
    return Response(status_code=204)


@router.get(
    "/installations/{team_id}/members",
    response_model=list[SlackMemberResponse],
)
async def list_workspace_members(
    team_id: str = Path(..., min_length=1, max_length=SLACK_TEAM_ID_MAX_LENGTH),
    session: AsyncSession = Depends(get_session),
) -> list[SlackMemberResponse]:
    """List non-bot, active members of a Slack workspace.

    Powers the dashboard's "pick a Slack member" picker when adding a
    user to the directory. Calls Slack's `users.list` via the stored
    bot token — requires `users:read` scope on the installation.

    Returns 404 if we don't have an installation for the team. 502 if
    Slack rejects the API call (missing scope, token revoked) —
    operator needs to reinstall. Bots, deactivated accounts, and the
    Slackbot pseudo-user are filtered out so the picker only shows
    humans the operator can actually assign tasks to.
    """
    client = await get_client_for_team(session, team_id)
    if client is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No Slack installation for team '{team_id}'. "
                "Install the awaithumans app to this workspace first."
            ),
        )

    try:
        resp = await client.users_list()
    except SlackApiError as exc:
        # Specific catch — see auth.test above for rationale.
        logger.warning("Slack users.list failed for team_id=%s: %s", team_id, exc)
        raise HTTPException(
            status_code=502,
            detail=(
                "Slack rejected users.list. The installation may be "
                "missing the 'users:read' scope, or the token was "
                "revoked. Reinstall the workspace from Settings → Slack."
            ),
        ) from exc

    out: list[SlackMemberResponse] = []
    for m in resp.data.get("members", []):  # type: ignore[attr-defined]
        if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
            continue
        profile = m.get("profile") or {}
        out.append(
            SlackMemberResponse(
                id=m["id"],
                name=m.get("name") or "",
                real_name=profile.get("real_name") or m.get("real_name"),
                display_name=profile.get("display_name") or None,
                is_admin=bool(m.get("is_admin") or m.get("is_owner")),
            )
        )

    # Stable sort — real_name, falling back to handle. Keeps the UI
    # order predictable across refetches.
    out.sort(key=lambda u: (u.real_name or u.name or u.id).lower())
    return out
