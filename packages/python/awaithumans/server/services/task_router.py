"""Task routing — resolve `assign_to` dict to a concrete user (Option C).

Three input shapes, resolved in priority order:

1. `assign_to = {"email": "..."}` — exact address. Router looks the
   user up to find the stable `user_id`, but falls through to
   "user not in directory" as a soft path (developer is being
   explicit; we trust them even without a matching user row). The
   task still gets `assigned_to_email` set.

2. `assign_to = {"role": "...", "access_level": "...", "pool": "..."}`
   — filter attributes. Router queries active users matching the
   filters and picks the **least recently assigned** one. That user's
   `last_assigned_at` is bumped in the same transaction so two
   concurrent tasks pick different users (single-writer SQLite) or
   race safely (Postgres — last committer wins on the row, second
   pick reads the fresh timestamp).

3. `assign_to = None` OR no matching user — first try to derive an
   implicit assignee from `notify` (see `derive_implicit_assignee`).
   If `notify` is a single Slack DM target that resolves to a
   directory user, they become the assignee. Otherwise the task
   stays unassigned (`assigned_to_email = None`, `assigned_to_user_id
   = None`); the operator can assign it manually from the dashboard,
   or a broadcast channel can be claimed by whoever picks it up first.

Notification semantics (read by the channels layer):

- If a user was resolved and `notify` is empty, the channels layer
  emits to all of the user's registered addresses (email if set,
  slack if set).
- If `notify` is provided explicitly, it's a complete override —
  nothing is auto-derived from the resolved user.

The router does NOT mutate the task row directly. It returns a dict
describing the resolution; `create_task` applies it. Keeps the router
testable independent of the task service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import User

logger = logging.getLogger("awaithumans.server.services.task_router")


@dataclass(frozen=True)
class RoutingResult:
    """What the router decided. Mirrors the fields `create_task` needs
    to set on the new task row."""

    user_id: str | None
    email: str | None
    # Not set on the task directly; returned for the channels layer
    # to consult when `notify` was empty and the router had to pick
    # implicit delivery addresses.
    slack_team_id: str | None = None
    slack_user_id: str | None = None


async def resolve_assign_to(
    session: AsyncSession,
    assign_to: dict[str, Any] | None,
) -> RoutingResult:
    """Resolve `assign_to` to a concrete user reference.

    Does NOT commit — caller is responsible for transaction boundaries.
    A successful role-based match bumps `last_assigned_at` on the picked
    user in the caller's transaction so fairness state advances only
    when the enclosing work commits.
    """
    if not assign_to:
        return RoutingResult(user_id=None, email=None)

    # Demo lane (public + hot): both AwaitVerify landing-demo priorities
    # route to a single dedicated reviewer via DEMO_REVIEWER_EMAIL. The
    # hot-lane Slack-channel ping is layered on top later in the
    # notification pipeline; routing-wise they share an inbox so the
    # founder sees every demo in one place. If the env var is unset we
    # fall through to normal AwaitVerify resolution rather than silently
    # dropping the task; an operator running the demo without the var
    # set is misconfigured but the task still needs an owner.
    if assign_to.get("managed") == "awaitverify" and assign_to.get("priority") in {
        "demo",
        "demo_hot",
    }:
        from awaithumans.server.core.config import settings

        if settings.DEMO_REVIEWER_EMAIL:
            user = await _get_by_email(session, settings.DEMO_REVIEWER_EMAIL)
            if user is not None:
                return _result_from_user(user)
            return RoutingResult(
                user_id=None,
                email=settings.DEMO_REVIEWER_EMAIL,
            )

    # Explicit-email path: developer handed us the address directly.
    explicit_email = assign_to.get("email")
    if explicit_email:
        user = await _get_by_email(session, explicit_email)
        if user is None:
            # Trust the developer — we'll route to the literal address.
            # The task's `assigned_to_user_id` stays null; routing works
            # via `assigned_to_email`. An operator seeing the row can
            # add the user later if they want the fairness tracking.
            return RoutingResult(user_id=None, email=explicit_email)
        return _result_from_user(user)

    # Marketplace path — out of scope for v1, pass through so nothing
    # routes to a non-existent user.
    if "marketplace" in assign_to:
        return RoutingResult(user_id=None, email=None)

    # Role/filter-based routing — pick the least-recently-assigned
    # active user matching the filters.
    role = assign_to.get("role")
    access_level = assign_to.get("access_level")
    pool = assign_to.get("pool")

    if not any([role, access_level, pool]):
        # No filters and no email — caller asked for nothing routable.
        return RoutingResult(user_id=None, email=None)

    picked = await _pick_least_recently_assigned(
        session, role=role, access_level=access_level, pool=pool
    )
    if picked is None:
        logger.info(
            "No user matched routing filters role=%r access_level=%r pool=%r",
            role,
            access_level,
            pool,
        )
        return RoutingResult(user_id=None, email=None)

    picked.last_assigned_at = datetime.now(timezone.utc)
    session.add(picked)

    return _result_from_user(picked)


async def _get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _pick_least_recently_assigned(
    session: AsyncSession,
    *,
    role: str | None,
    access_level: str | None,
    pool: str | None,
) -> User | None:
    """Pick the active user with the oldest `last_assigned_at`.

    Null sorts first (SQLite's default is NULLS FIRST for ASC; Postgres
    defaults to NULLS LAST, so we pin it explicitly). Fresh hires never
    get picked last by accident on Postgres.

    On Postgres we could add `FOR UPDATE SKIP LOCKED` to serialize
    pickers and get stronger fairness under concurrency. Not needed on
    SQLite (single-writer). Deferred to post-launch — v1 accepts that
    two concurrent pickers may land on the same user, the next task
    then picks someone else as the timestamp has been bumped.
    """
    stmt = (
        select(User)
        .where(User.active == True)  # noqa: E712 — SQLAlchemy idiom
        .order_by(User.last_assigned_at.asc().nulls_first(), User.created_at.asc())
        .limit(1)
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if access_level is not None:
        stmt = stmt.where(User.access_level == access_level)
    if pool is not None:
        stmt = stmt.where(User.pool == pool)

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _result_from_user(user: User) -> RoutingResult:
    return RoutingResult(
        user_id=user.id,
        email=user.email,
        slack_team_id=user.slack_team_id,
        slack_user_id=user.slack_user_id,
    )


# ─── Implicit assignee from notify ───────────────────────────────────


async def derive_implicit_assignee(
    session: AsyncSession,
    notify: list[str] | None,
) -> RoutingResult:
    """Infer an assignee when the developer used `notify=` to point at
    a single specific person but didn't pass `assign_to=`.

    `notify=["slack:@alice"]` means "send a DM to alice." The
    developer's mental model is *also* "alice is responsible for
    this task." Without inference, the dashboard's "Assigned to"
    column is empty and the Slack auth check rejects alice's
    submission ("not assigned to you"). The fix: derive the
    assignee at task-creation time when:

      - notify has exactly one entry, AND
      - that entry is a Slack channel route, AND
      - the target is a DM (handle, email, or user_id — not a
        channel sigil), AND
      - the target resolves to a real Slack user, AND
      - that user exists in the awaithumans directory and is active.

    Anything else returns an empty `RoutingResult` and the task
    stays unassigned. Notably:

      - Multiple notify entries → ambiguous; don't guess.
      - `slack:#channel` → broadcast; the claim flow is the right
        path.
      - Resolves to a Slack user not in the directory → can't pin
        an assignee_user_id; we still post the DM (the notifier
        has its own resolution layer), but the task stays
        unassigned. Operator can add the user later if they want
        the routing tracked.
    """
    if not notify or len(notify) != 1:
        return RoutingResult(user_id=None, email=None)

    # Lazy import to avoid pulling routing into modules that don't
    # need the channels layer (e.g. CLI).
    from awaithumans.server.channels.routing import parse_route
    from awaithumans.server.channels.slack.client import (
        get_client_for_team,
        get_default_client,
    )
    from awaithumans.server.channels.slack.resolution import resolve_slack_target

    route = parse_route(notify[0])
    if route is None:
        return RoutingResult(user_id=None, email=None)

    # Email path — `notify=["email+id:reviewer@acme.com"]` means
    # "send a review email to that address." If the address is
    # already in the directory we pin them as the assignee at
    # task-create time. If not, the email handoff endpoint
    # claims the task on first click (see `routes/auth.py`).
    if route.channel == "email":
        user = await _get_by_email(session, route.target.lower())
        if user is None or not user.active:
            return RoutingResult(user_id=None, email=route.target)
        return _result_from_user(user)

    if route.channel != "slack":
        return RoutingResult(user_id=None, email=None)

    # Channel sigils (`#general`, raw `C…`/`G…`) are broadcasts; the
    # claim flow assigns at click time, not creation time.
    target = route.target
    body = target.removeprefix("@") if target.startswith("@") else target
    if target.startswith("#") or (body.startswith(("C", "G")) and body[1:].isalnum()):
        return RoutingResult(user_id=None, email=None)

    # Resolve target → real Slack user_id. May call users.list /
    # users.lookupByEmail under the hood; cached per-team.
    client = (
        await get_client_for_team(session, route.identity)
        if route.identity
        else await get_default_client(session)
    )
    if client is None:
        # No Slack client configured — nothing to derive.
        return RoutingResult(user_id=None, email=None)

    resolved = await resolve_slack_target(
        client=client, target=route.target, team_id=route.identity
    )
    if resolved is None or not resolved.startswith(("U", "W")):
        return RoutingResult(user_id=None, email=None)

    # Look up directory user by slack_user_id. team_id constraint is
    # nice-to-have but not required — a user_id is workspace-unique
    # in practice for self-hosted single-workspace setups, and the
    # multi-workspace case still has the unique constraint on
    # (slack_team_id, slack_user_id) so over-matching is impossible.
    user = await _get_by_slack_user_id(
        session,
        slack_user_id=resolved,
        slack_team_id=route.identity,
    )
    if user is None or not user.active:
        logger.info(
            "derive_implicit_assignee: notify=%s resolved to %s but no "
            "active directory user matches; task stays unassigned.",
            notify[0],
            resolved,
        )
        return RoutingResult(user_id=None, email=None)

    logger.info(
        "derive_implicit_assignee: notify=%s → user_id=%s (%s)",
        notify[0],
        user.id,
        user.email or user.display_name or user.id,
    )
    return _result_from_user(user)


async def _get_by_slack_user_id(
    session: AsyncSession,
    *,
    slack_user_id: str,
    slack_team_id: str | None,
) -> User | None:
    """Look up a directory user by Slack user_id.

    When `slack_team_id` is provided (multi-workspace OAuth case),
    we filter on both — the unique constraint is on the pair. When
    it's None (static-token / default workspace), we filter only
    on user_id. In static-token mode there's only one workspace by
    construction so the over-match risk is zero."""
    stmt = select(User).where(User.slack_user_id == slack_user_id)
    if slack_team_id:
        stmt = stmt.where(User.slack_team_id == slack_team_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
