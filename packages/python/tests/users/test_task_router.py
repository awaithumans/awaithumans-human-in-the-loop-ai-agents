"""Task router — resolve `assign_to` dict to a User (Option C).

Covers the four paths:
  1. explicit email
  2. role/access_level/pool filter → least-recently-assigned pick
  3. no match (filters match nothing)
  4. null / unroutable assign_to

Plus fairness: calling the router twice with the same filter should
pick a different user each time, so `last_assigned_at` is actually
doing work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.services.task_router import (
    RoutingResult,
    resolve_assign_to,
)
from awaithumans.server.services.user_service import create_user, get_user

# ─── Null / unroutable inputs ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_null_assign_to_returns_empty(session: AsyncSession) -> None:
    result = await resolve_assign_to(session, None)
    assert result == RoutingResult(user_id=None, email=None)


@pytest.mark.asyncio
async def test_empty_dict_returns_empty(session: AsyncSession) -> None:
    result = await resolve_assign_to(session, {})
    assert result == RoutingResult(user_id=None, email=None)


@pytest.mark.asyncio
async def test_marketplace_stub_returns_empty(session: AsyncSession) -> None:
    """Marketplace is reserved for post-launch; router must not pretend
    to route to it."""
    result = await resolve_assign_to(session, {"marketplace": "global"})
    assert result.user_id is None
    assert result.email is None


# ─── Explicit email ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_email_known_user(session: AsyncSession) -> None:
    u = await create_user(session, email="alice@example.com", role="reviewer")
    result = await resolve_assign_to(session, {"email": "alice@example.com"})
    assert result.user_id == u.id
    assert result.email == "alice@example.com"


@pytest.mark.asyncio
async def test_explicit_email_unknown_user_still_routes(
    session: AsyncSession,
) -> None:
    """Developer passes an email we don't have in the directory yet —
    we still route by email, `assigned_to_user_id` just stays null."""
    result = await resolve_assign_to(session, {"email": "stranger@example.com"})
    assert result.user_id is None
    assert result.email == "stranger@example.com"


# ─── Role-based routing (Option C) ───────────────────────────────────


@pytest.mark.asyncio
async def test_role_filter_picks_active_matching_user(
    session: AsyncSession,
) -> None:
    # Inactive user should be skipped even if they match the role.
    await create_user(session, email="inactive@x.com", role="kyc", active=False)
    picked_user = await create_user(session, email="active@x.com", role="kyc")

    result = await resolve_assign_to(session, {"role": "kyc"})
    assert result.user_id == picked_user.id


@pytest.mark.asyncio
async def test_no_matching_user_returns_empty(session: AsyncSession) -> None:
    await create_user(session, email="alice@x.com", role="kyc")
    result = await resolve_assign_to(session, {"role": "support"})
    assert result.user_id is None
    assert result.email is None


@pytest.mark.asyncio
async def test_new_user_wins_on_first_task(session: AsyncSession) -> None:
    """A user with NULL last_assigned_at sorts first — their queue is
    empty by definition."""
    # Seasoned user: assigned a while ago.
    old = await create_user(session, email="old@x.com", role="kyc")
    old.last_assigned_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.add(old)
    await session.commit()

    # New hire: last_assigned_at is NULL.
    new_hire = await create_user(session, email="newbie@x.com", role="kyc")

    result = await resolve_assign_to(session, {"role": "kyc"})
    assert result.user_id == new_hire.id


@pytest.mark.asyncio
async def test_router_bumps_last_assigned_at(session: AsyncSession) -> None:
    """After the router picks a user, their `last_assigned_at` must
    advance so the NEXT call picks somebody else (fairness)."""
    a = await create_user(session, email="a@x.com", role="kyc")
    b = await create_user(session, email="b@x.com", role="kyc")

    # First call should pick one of them (deterministic: oldest
    # created_at wins on NULLS-FIRST + created_at tiebreak).
    first = await resolve_assign_to(session, {"role": "kyc"})
    await session.commit()
    assert first.user_id in {a.id, b.id}

    # Second call: the other user should be picked.
    second = await resolve_assign_to(session, {"role": "kyc"})
    await session.commit()
    assert second.user_id in {a.id, b.id}
    assert second.user_id != first.user_id


@pytest.mark.asyncio
async def test_access_level_and_pool_filters_compose(
    session: AsyncSession,
) -> None:
    await create_user(session, email="j@x.com", role="kyc", access_level="junior", pool="ops")
    senior_match = await create_user(
        session, email="s@x.com", role="kyc", access_level="senior", pool="ops"
    )
    # Correct role + access_level + wrong pool — excluded.
    await create_user(session, email="s2@x.com", role="kyc", access_level="senior", pool="fraud")

    result = await resolve_assign_to(
        session, {"role": "kyc", "access_level": "senior", "pool": "ops"}
    )
    assert result.user_id == senior_match.id


@pytest.mark.asyncio
async def test_slack_only_user_is_routable(session: AsyncSession) -> None:
    """The router works with users that have no email — the channels
    layer uses the returned slack identifiers instead."""
    slack_user = await create_user(
        session,
        slack_team_id="T01",
        slack_user_id="U01",
        role="kyc",
        display_name="Slack-only",
    )

    result = await resolve_assign_to(session, {"role": "kyc"})
    assert result.user_id == slack_user.id
    assert result.email is None
    assert result.slack_team_id == "T01"
    assert result.slack_user_id == "U01"


@pytest.mark.asyncio
async def test_inactive_user_never_picked(session: AsyncSession) -> None:
    """Only matching user is inactive → empty result, not the inactive
    row masquerading as available."""
    u = await create_user(session, email="out@x.com", role="kyc")
    u.active = False
    session.add(u)
    await session.commit()

    result = await resolve_assign_to(session, {"role": "kyc"})
    assert result.user_id is None


@pytest.mark.asyncio
async def test_router_persists_bump_on_commit(session: AsyncSession) -> None:
    """The router stages the `last_assigned_at` bump via session.add;
    caller's commit is what makes it durable."""
    picked = await create_user(session, email="a@x.com", role="kyc")
    assert picked.last_assigned_at is None

    await resolve_assign_to(session, {"role": "kyc"})
    await session.commit()

    fresh = await get_user(session, picked.id)
    assert fresh is not None
    assert fresh.last_assigned_at is not None
