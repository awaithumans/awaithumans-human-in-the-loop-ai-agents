"""Dashboard auth routes — login, logout, session introspection.

Public endpoints (these are what the rest of the API needs to be auth'd):

    POST   /api/auth/login     { email, password }     → 204 + cookie
    POST   /api/auth/logout                             → 204 + clear cookie
    GET    /api/auth/me                                 → { authenticated, user }

No `auth_enabled: false` path anymore — auth is always on in v1. The
dashboard uses `/api/setup/status` to distinguish first-run (show the
`/setup` wizard) from normal (show the login form).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.channels.email.templates.renderers import (
    handoff_error_page_html,
)
from awaithumans.server.core.auth import (
    InvalidSessionError,
    sign_session,
    verify_session,
)
from awaithumans.server.core.config import settings
from awaithumans.server.core.email_handoff import (
    InvalidHandoffError as InvalidEmailHandoffError,
)
from awaithumans.server.core.email_handoff import (
    verify_handoff as verify_email_handoff,
)
from awaithumans.server.core.password import dummy_verify, verify_password
from awaithumans.server.core.rate_limit import (
    LOGIN_PER_EMAIL,
    LOGIN_PER_IP,
    client_ip,
)
from awaithumans.server.core.slack_handoff import (
    InvalidHandoffError,
    verify_handoff,
)
from awaithumans.server.db.connection import get_session
from awaithumans.server.schemas.auth import LoginRequest, MeResponse
from awaithumans.server.services.user_service import (
    create_user,
    get_user,
    get_user_by_email,
)
from awaithumans.utils.constants import (
    DASHBOARD_SESSION_COOKIE_NAME,
    DASHBOARD_SESSION_MAX_AGE_SECONDS,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("awaithumans.server.routes.auth")


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Verify credentials against the user directory and set a signed
    session cookie.

    Returns 401 for unknown email, inactive account, no password set,
    or wrong password — uniform error so we don't leak which field
    was wrong. Returns 429 once an IP or email exceeds its sliding-
    window limit.
    """
    # Rate-limit on BOTH dimensions: per-IP catches credential
    # stuffing from one host (different emails each attempt); per-
    # email catches a distributed attack against one known account.
    # Reject before touching argon2 so we don't burn CPU on attempts
    # the limiter has already decided to refuse.
    ip = client_ip(request)
    email_key = body.email.lower().strip()
    if not LOGIN_PER_IP.check(f"login:{ip}"):
        logger.warning("Login rate-limited by IP: %s", ip)
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again in a few minutes.",
        )
    if not LOGIN_PER_EMAIL.check(f"login:{email_key}"):
        logger.warning("Login rate-limited by email: %s", email_key)
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again in a few minutes.",
        )

    user = await get_user_by_email(session, body.email)
    reject = HTTPException(status_code=401, detail="Invalid credentials.")

    # Timing equalization: without this, the unknown-user path skips
    # argon2 and returns ~100ms faster than the known-user path,
    # letting an attacker probe for registered emails. Burn the same
    # CPU budget either way.
    if user is None or not user.active or not user.password_hash:
        dummy_verify(body.password)
        logger.info("Failed login (no match / inactive / no password): %s", body.email)
        raise reject

    if not verify_password(body.password, user.password_hash):
        logger.info("Failed login (wrong password): %s", body.email)
        raise reject

    # Successful login — clear the per-email counter so a legit user
    # who fat-fingered their password three times doesn't get
    # throttled out of their own session 5 minutes later.
    LOGIN_PER_EMAIL.reset(f"login:{email_key}")

    token = sign_session(user_id=user.id, is_operator=user.is_operator)
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE_NAME,
        value=token,
        max_age=DASHBOARD_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.PUBLIC_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(DASHBOARD_SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    """Session introspection. Returns the current user record when
    signed in, or `authenticated=false` otherwise. Dashboard mounts
    call this to decide whether to render the queue or the login form."""
    cookie = request.cookies.get(DASHBOARD_SESSION_COOKIE_NAME)
    if not cookie:
        return MeResponse(authenticated=False)

    try:
        claims = verify_session(cookie)
    except InvalidSessionError:
        return MeResponse(authenticated=False)

    # Fresh read — if the user was deleted or deactivated, treat as
    # logged out. The cookie's `is_operator` claim is overridden by
    # the current DB value to keep role changes snappy.
    user = await get_user(session, claims.user_id)
    if user is None or not user.active:
        return MeResponse(authenticated=False)

    return MeResponse(
        authenticated=True,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_operator=user.is_operator,
    )


@router.get("/slack-handoff")
async def slack_handoff(
    request: Request,
    u: str = Query(..., description="Directory user_id the URL was issued to."),
    t: str = Query(..., description="Task id the URL is bound to."),
    e: int = Query(..., description="Unix expiry — usually task.timeout_at."),
    s: str = Query(..., description="HMAC signature over (u|t|e)."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Sign in a Slack-only user and redirect to the task page.

    Endpoint is invoked by the recipient clicking "Open in Dashboard"
    on the Slack DM their task notification arrived in. Slack-only
    users have no password, so the signed URL is their only path
    through the login wall. Operators / email-and-password reviewers
    can also click the link and end up logged in as themselves; the
    URL is not a privilege escalator — it always mints a session for
    user_id `u` exactly.
    """
    try:
        verify_handoff(user_id=u, task_id=t, exp_unix=e, signature=s)
    except InvalidHandoffError as exc:
        logger.info("Rejected Slack handoff: %s", exc)
        # Recipients of Slack DMs click these in a browser — they're
        # not developers and should never see raw FastAPI JSON.
        # Render the same explanation as a brand-styled page.
        return HTMLResponse(
            content=handoff_error_page_html(
                heading="Sign-in link expired",
                message=(
                    "This link is invalid or expired. Open the latest "
                    "Slack notification for this task to get a fresh one."
                ),
                hint=(
                    "If the task has already been completed or has timed "
                    "out, check the dashboard for its final status."
                ),
            ),
            status_code=400,
        )

    user = await get_user(session, u)
    if user is None or not user.active:
        # Don't leak which case it is — both look the same to a
        # would-be attacker probing user_ids, and the operator can
        # tell from logs.
        logger.info("Slack handoff rejected: user=%s missing/inactive", u)
        raise HTTPException(
            status_code=403,
            detail="That user is no longer active. Ask an operator to re-add you to the directory.",
        )

    token = sign_session(user_id=user.id, is_operator=user.is_operator)
    target = f"/task?id={t}"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE_NAME,
        value=token,
        max_age=DASHBOARD_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.PUBLIC_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )
    logger.info("Slack handoff: signed in user=%s for task=%s", user.id, t)
    return response


@router.get("/email-handoff")
async def email_handoff(
    request: Request,
    to: str = Query(..., description="Recipient email the URL was issued to."),
    t: str = Query(..., description="Task id the URL is bound to."),
    e: int = Query(..., description="Unix expiry — usually task.timeout_at."),
    s: str = Query(..., description="HMAC signature over (to|t|e)."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Sign in an email recipient and redirect to the task page.

    Mirror of `/api/auth/slack-handoff` for the email channel. The
    "Review in dashboard" link in a notification email points here so
    the recipient can clear the dashboard login wall even when their
    email isn't a registered reviewer yet.

    Auto-provisioning: if no directory user has the recipient's email,
    we create a passwordless reviewer on first click. Same trust
    boundary as task creation — the agent's `notify=` already
    expressed intent to delegate to that address. Operators can
    deactivate or delete the row later if they don't want it.

    The minted session is for that user exactly. The URL is not a
    privilege escalator — auto-created users are reviewers, never
    operators.
    """
    try:
        verify_email_handoff(recipient=to, task_id=t, exp_unix=e, signature=s)
    except InvalidEmailHandoffError as exc:
        logger.info("Rejected email handoff: %s", exc)
        # Recipients of notification emails click these in a browser —
        # they're not developers and should never see raw FastAPI JSON.
        # Render the same explanation as a brand-styled page.
        return HTMLResponse(
            content=handoff_error_page_html(
                heading="Sign-in link expired",
                message=(
                    "This link is invalid or expired. Open the latest "
                    "notification email for this task to get a fresh one."
                ),
                hint=(
                    "If the task has already been completed or has timed "
                    "out, check the dashboard for its final status."
                ),
            ),
            status_code=400,
        )

    normalized_email = to.lower()
    user = await get_user_by_email(session, normalized_email)

    if user is None:
        # Auto-provision a passwordless reviewer. The agent already
        # vetted this address by passing it to `notify=`; we trust
        # that boundary the same way we trust the agent's task
        # creation.
        user = await create_user(
            session,
            email=normalized_email,
            display_name=None,
            is_operator=False,
            password=None,
        )
        logger.info(
            "Email handoff: auto-provisioned reviewer for %s on task=%s",
            normalized_email,
            t,
        )
    elif not user.active:
        # Don't auto-reactivate — the operator may have deliberately
        # taken this user offline. Same response shape as the Slack
        # handoff so attackers probing user state get a uniform 403.
        logger.info("Email handoff rejected: user=%s inactive", normalized_email)
        raise HTTPException(
            status_code=403,
            detail="That reviewer is no longer active. Ask an operator "
            "to re-add you to the directory.",
        )

    # Claim the task for the recipient when nobody owns it yet.
    # Without this, the auto-provisioned reviewer would land on the
    # dashboard and immediately see "Couldn't load task" — they're
    # signed in but `require_task_read` denies them because they're
    # neither operator nor assignee. The handoff URL is itself proof
    # they had access to the email; promoting them to assignee on
    # first click is the same intent shape the Slack DM flow
    # encodes via the implicit-assignee derivation at create time.
    await _claim_task_for_recipient_if_unassigned(
        session, task_id=t, user_id=user.id, user_email=normalized_email
    )

    token = sign_session(user_id=user.id, is_operator=user.is_operator)
    target = f"/task?id={t}"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE_NAME,
        value=token,
        max_age=DASHBOARD_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.PUBLIC_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )
    logger.info("Email handoff: signed in user=%s for task=%s", user.id, t)
    return response


async def _claim_task_for_recipient_if_unassigned(
    session: AsyncSession,
    *,
    task_id: str,
    user_id: str,
    user_email: str,
) -> None:
    """First-writer-wins update — sets `assigned_to_user_id` only when
    the row is currently null. Two recipients clicking simultaneously
    is exceedingly rare for email (typical notify list is one address)
    but we still race-safe via the WHERE clause.

    Best-effort: if the task was deleted, terminal, or already assigned
    we no-op. The handoff still mints the session — operators who want
    to read other people's tasks are operators, not auto-provisioned
    reviewers.
    """
    from sqlalchemy import update

    from awaithumans.server.db.models import Task
    from awaithumans.utils.constants import TERMINAL_STATUSES_SET

    result = await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .where(Task.assigned_to_user_id.is_(None))
        .where(Task.status.notin_(list(TERMINAL_STATUSES_SET)))
        .values(assigned_to_user_id=user_id, assigned_to_email=user_email)
    )
    if result.rowcount > 0:
        await session.commit()
        logger.info(
            "Email handoff: claimed task=%s for user=%s",
            task_id,
            user_id,
        )
