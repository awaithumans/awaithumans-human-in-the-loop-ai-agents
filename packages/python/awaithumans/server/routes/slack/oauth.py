"""Slack OAuth install flow — `GET /oauth/start` + `GET /oauth/callback`.

Multi-workspace install. `/oauth/start` redirects to Slack's consent
page with a signed state parameter. `/oauth/callback` verifies the
state, exchanges the `code` for an access token via `oauth.v2.access`,
and upserts a SlackInstallation row keyed by team_id. Subsequent posts
to that workspace read the token from the DB.

Security model:

1. `/oauth/start` requires `?install_token=X` matching SLACK_INSTALL_TOKEN,
   compared in constant time. Without this, any visitor who knows
   PUBLIC_URL could install *their own* workspace into the DB and — if
   they win the "which install is the default" resolution — receive
   this server's task notifications. Constant-time compare avoids
   leaking the token via timing.
2. `/oauth/start` also returns 503 when SLACK_BOT_TOKEN is set — that
   means the operator picked single-workspace mode, so OAuth is off by
   construction. No way to trick OAuth into running alongside a static
   token.
3. State is cookie-bound. `/oauth/start` sets an httponly+Secure+
   SameSite=Lax cookie holding the state value; `/oauth/callback`
   compares the state query param to the cookie (constant-time), then
   verifies the HMAC signature, then checks expiry. Attacker who signs
   their own state in their own browser can't forge a matching cookie
   in the operator's browser.
4. Redirect query strings use urlencode() — team_name and error codes
   from Slack are encoded before landing in the URL.

These defenses assume the operator keeps SLACK_INSTALL_TOKEN secret.
For public hosted deployments, this install-token scheme needs to be
replaced with real user auth + per-tenant routing (see BUILD_NOTES §4).
"""

from __future__ import annotations

import hmac
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.channels.slack.oauth_state import sign_state, verify_state
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.services.slack_installation_service import (
    upsert_installation,
)
from awaithumans.utils.constants import (
    SLACK_DEFAULT_OAUTH_SCOPES,
    SLACK_OAUTH_ERROR_PARAM_MAX_LENGTH,
    SLACK_OAUTH_HTTP_TIMEOUT_SECONDS,
    SLACK_OAUTH_STATE_COOKIE_NAME,
    SLACK_OAUTH_STATE_MAX_AGE_SECONDS,
)

router = APIRouter()
logger = logging.getLogger("awaithumans.server.routes.slack.oauth")


def _oauth_redirect_uri() -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}/api/channels/slack/oauth/callback"


def _oauth_scopes() -> str:
    return settings.SLACK_OAUTH_SCOPES or SLACK_DEFAULT_OAUTH_SCOPES


def _oauth_cookie_secure() -> bool:
    """HTTPS-only cookie unless the server is explicitly on plain HTTP (dev)."""
    return settings.PUBLIC_URL.startswith("https://")


def _error_redirect(code: str) -> RedirectResponse:
    """Redirect to dashboard home with a URL-encoded error param."""
    qs = urlencode({"slack_oauth_error": code[:SLACK_OAUTH_ERROR_PARAM_MAX_LENGTH]})
    return RedirectResponse(url=f"{settings.PUBLIC_URL.rstrip('/')}/?{qs}")


@router.get("/oauth/start")
async def oauth_start(install_token: str | None = None) -> RedirectResponse:
    """Kick off the Slack OAuth consent flow.

    Requires `?install_token=X` matching AWAITHUMANS_SLACK_INSTALL_TOKEN.
    Without that check, anyone who knows PUBLIC_URL could install their
    own workspace into the server's DB.
    """
    if settings.SLACK_BOT_TOKEN:
        # Single-workspace mode chosen by operator; OAuth must not run.
        raise HTTPException(
            status_code=503,
            detail=(
                "Server is in single-workspace mode (SLACK_BOT_TOKEN is set). "
                "Unset it to enable the OAuth install flow."
            ),
        )
    if (
        not settings.SLACK_CLIENT_ID
        or not settings.SLACK_CLIENT_SECRET
        or not settings.SLACK_SIGNING_SECRET
        or not settings.SLACK_INSTALL_TOKEN
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "Slack OAuth is not configured. Set "
                "AWAITHUMANS_SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, "
                "SLACK_SIGNING_SECRET, and SLACK_INSTALL_TOKEN."
            ),
        )

    # Operator-only gate. Constant-time compare to avoid token-length leaks.
    if install_token is None or not hmac.compare_digest(
        install_token, settings.SLACK_INSTALL_TOKEN
    ):
        raise HTTPException(status_code=403, detail="Install token required.")

    state = sign_state(settings.SLACK_SIGNING_SECRET)
    params = {
        "client_id": settings.SLACK_CLIENT_ID,
        "scope": _oauth_scopes(),
        "state": state,
        "redirect_uri": _oauth_redirect_uri(),
    }
    response = RedirectResponse(url=f"https://slack.com/oauth/v2/authorize?{urlencode(params)}")
    # Double-submit cookie: callback will require this to match the `state`
    # query param Slack sends back.
    response.set_cookie(
        key=SLACK_OAUTH_STATE_COOKIE_NAME,
        value=state,
        max_age=SLACK_OAUTH_STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_oauth_cookie_secure(),
        samesite="lax",
        path="/api/channels/slack/oauth",
    )
    return response


@router.get("/oauth/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    cookie_state: str | None = Cookie(default=None, alias=SLACK_OAUTH_STATE_COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Finish the OAuth flow: verify state, exchange code, persist tokens."""
    if error:
        return _error_redirect(error)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")

    # 1) State must match the cookie set by /oauth/start. Constant-time.
    #    An attacker who minted a state in their own browser can't set this
    #    cookie in the operator's browser (SameSite=Lax + Secure).
    if not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise HTTPException(status_code=401, detail="OAuth state mismatch.")

    # 2) State must carry a valid HMAC and not be expired.
    if not settings.SLACK_SIGNING_SECRET or not verify_state(state, settings.SLACK_SIGNING_SECRET):
        raise HTTPException(status_code=401, detail="Invalid OAuth state.")

    if not settings.SLACK_CLIENT_ID or not settings.SLACK_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Slack OAuth credentials not configured.")

    async with httpx.AsyncClient(timeout=SLACK_OAUTH_HTTP_TIMEOUT_SECONDS) as http:
        resp = await http.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": _oauth_redirect_uri(),
            },
        )

    data = resp.json()
    if not data.get("ok"):
        logger.error("Slack oauth.v2.access failed: %s", data.get("error"))
        response = _error_redirect(str(data.get("error", "unknown")))
        response.delete_cookie(SLACK_OAUTH_STATE_COOKIE_NAME, path="/api/channels/slack/oauth")
        return response

    team = data.get("team") or {}
    authed_user = data.get("authed_user") or {}
    enterprise = data.get("enterprise") or {}

    team_id = team.get("id")
    bot_token = data.get("access_token")
    bot_user_id = data.get("bot_user_id")
    if not team_id or not bot_token or not bot_user_id:
        logger.error("oauth.v2.access response missing required fields: %s", data)
        raise HTTPException(
            status_code=502, detail="Slack returned an incomplete install response."
        )

    await upsert_installation(
        session,
        team_id=team_id,
        team_name=team.get("name"),
        bot_token=bot_token,
        bot_user_id=bot_user_id,
        scopes=data.get("scope", ""),
        enterprise_id=enterprise.get("id"),
        installed_by_user_id=authed_user.get("id"),
    )
    logger.info("Slack installed for team %s (%s)", team_id, team.get("name"))

    qs = urlencode({"slack_installed": team.get("name") or team_id})
    response = RedirectResponse(url=f"{settings.PUBLIC_URL.rstrip('/')}/?{qs}")
    # Single-use cookie — invalidate immediately so replays fail.
    response.delete_cookie(SLACK_OAUTH_STATE_COOKIE_NAME, path="/api/channels/slack/oauth")
    return response
