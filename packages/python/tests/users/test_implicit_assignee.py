"""Implicit assignee derivation from `notify=` Slack DM target.

The contract: when a developer writes
`notify=["slack:@alice"]` and doesn't pass `assign_to=`, the task
should treat alice as the assignee. Without this, the dashboard's
"Assigned to" stays empty AND the Slack auth check rejects alice's
submission ("not assigned to you").

Three things the tests pin:

  - Single Slack DM target → assignee derived (handle, email, U_ID).
  - Channel sigils, multi-target notify lists, and unknown directory
    users all stay unassigned (correct fallback to claim flow).
  - When `assign_to=` IS provided, derivation never overrides — the
    explicit assignment wins.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.channels.slack.resolution import clear_handle_cache
from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
    User,
)
from awaithumans.server.services.task_router import derive_implicit_assignee


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_handle_cache()
    yield
    clear_handle_cache()


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str | None = None,
    slack_user_id: str | None = None,
    active: bool = True,
) -> User:
    user = User(
        email=email,
        slack_user_id=slack_user_id,
        slack_team_id=None,
        active=active,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _patch_slack(*, resolved_user_id: str | None):
    """Patch the channel layer so derive_implicit_assignee can run
    without a real Slack client. Returns the patches as a context."""
    fake_client = object()
    return [
        patch(
            "awaithumans.server.channels.slack.client.get_default_client",
            new=AsyncMock(return_value=fake_client),
        ),
        patch(
            "awaithumans.server.channels.slack.client.get_client_for_team",
            new=AsyncMock(return_value=fake_client),
        ),
        patch(
            "awaithumans.server.channels.slack.resolution.resolve_slack_target",
            new=AsyncMock(return_value=resolved_user_id),
        ),
    ]


@pytest.mark.asyncio
async def test_handle_with_directory_match_becomes_assignee(
    session: AsyncSession,
) -> None:
    alice = await _seed_user(session, email="alice@acme.com", slack_user_id="U_ALICE")
    with (
        _patch_slack(resolved_user_id="U_ALICE")[0],
        _patch_slack(resolved_user_id="U_ALICE")[1],
        _patch_slack(resolved_user_id="U_ALICE")[2],
    ):
        result = await derive_implicit_assignee(session, ["slack:@alice"])

    assert result.user_id == alice.id
    assert result.email == "alice@acme.com"


@pytest.mark.asyncio
async def test_email_target_resolves_to_directory_user(
    session: AsyncSession,
) -> None:
    bob = await _seed_user(session, email="bob@acme.com", slack_user_id="U_BOB")
    with (
        _patch_slack(resolved_user_id="U_BOB")[0],
        _patch_slack(resolved_user_id="U_BOB")[1],
        _patch_slack(resolved_user_id="U_BOB")[2],
    ):
        result = await derive_implicit_assignee(session, ["slack:bob@acme.com"])

    assert result.user_id == bob.id


@pytest.mark.asyncio
async def test_user_id_target_resolves_directly(
    session: AsyncSession,
) -> None:
    """`slack:@U_ALICE` → no need to call Slack API; the resolver
    returns the user_id as-is. The directory lookup still has to find
    the row."""
    alice = await _seed_user(session, slack_user_id="U_ALICE")
    with (
        _patch_slack(resolved_user_id="U_ALICE")[0],
        _patch_slack(resolved_user_id="U_ALICE")[1],
        _patch_slack(resolved_user_id="U_ALICE")[2],
    ):
        result = await derive_implicit_assignee(session, ["slack:@U_ALICE"])

    assert result.user_id == alice.id


@pytest.mark.asyncio
async def test_channel_sigil_does_not_derive(session: AsyncSession) -> None:
    """`slack:#approvals` is a broadcast — first-claim-wins flow handles
    assignment at click time, not at task creation. We must NOT pick
    a random directory user."""
    await _seed_user(session, email="alice@acme.com", slack_user_id="U_ALICE")
    result = await derive_implicit_assignee(session, ["slack:#approvals"])
    assert result.user_id is None
    assert result.email is None


@pytest.mark.asyncio
async def test_multiple_notify_entries_does_not_derive(
    session: AsyncSession,
) -> None:
    """Multi-channel notify is ambiguous — operator clearly wanted
    notification fan-out. Picking one as "the" assignee would be
    wrong; stay unassigned and let the operator route manually if
    they care."""
    await _seed_user(session, email="alice@acme.com", slack_user_id="U_ALICE")
    result = await derive_implicit_assignee(session, ["slack:@alice", "email:bob@acme.com"])
    assert result.user_id is None


@pytest.mark.asyncio
async def test_target_not_in_directory_returns_empty(
    session: AsyncSession,
) -> None:
    """Slack resolves @ghost → U_GHOST, but no directory user has
    that slack_user_id. The notifier still posts the DM (it does
    its own resolution); but we can't pin an `assigned_to_user_id`
    so the task stays unassigned. Operator can add the user later."""
    with (
        _patch_slack(resolved_user_id="U_GHOST")[0],
        _patch_slack(resolved_user_id="U_GHOST")[1],
        _patch_slack(resolved_user_id="U_GHOST")[2],
    ):
        result = await derive_implicit_assignee(session, ["slack:@ghost"])
    assert result.user_id is None


@pytest.mark.asyncio
async def test_inactive_directory_user_skipped(
    session: AsyncSession,
) -> None:
    """An inactive user in the directory must not be assigned a new
    task — that's the whole point of toggling `active=False`."""
    await _seed_user(session, email="alice@acme.com", slack_user_id="U_ALICE", active=False)
    with (
        _patch_slack(resolved_user_id="U_ALICE")[0],
        _patch_slack(resolved_user_id="U_ALICE")[1],
        _patch_slack(resolved_user_id="U_ALICE")[2],
    ):
        result = await derive_implicit_assignee(session, ["slack:@alice"])
    assert result.user_id is None


@pytest.mark.asyncio
async def test_email_channel_derives_when_recipient_is_directory_user(
    session: AsyncSession,
) -> None:
    """`notify=["email:alice@..."]` → look up alice in the directory
    and pin her as assignee, mirroring the Slack DM derivation.
    Without this, the auto-provisioned-on-handoff flow would always
    have to claim — for recipients who already have directory rows
    the create-time path is cleaner."""
    user = await _seed_user(session, email="alice@acme.com")
    result = await derive_implicit_assignee(session, ["email:alice@acme.com"])
    assert result.user_id == user.id
    assert result.email == user.email


@pytest.mark.asyncio
async def test_email_channel_with_unknown_recipient_stays_unassigned(
    session: AsyncSession,
) -> None:
    """Recipient isn't in the directory yet → derivation can't pin
    a user_id but still threads the address through so the email
    channel knows where to send. The handoff endpoint claims at
    click time when the user is auto-provisioned."""
    result = await derive_implicit_assignee(session, ["email:nobody@acme.com"])
    assert result.user_id is None
    assert result.email == "nobody@acme.com"


@pytest.mark.asyncio
async def test_email_channel_handles_identity_suffix(
    session: AsyncSession,
) -> None:
    """`notify=["email+acme:alice@..."]` (identity-suffixed) works
    the same way — derivation looks up the recipient regardless of
    which identity routes the email."""
    user = await _seed_user(session, email="alice@acme.com")
    result = await derive_implicit_assignee(session, ["email+acme-prod:alice@acme.com"])
    assert result.user_id == user.id


@pytest.mark.asyncio
async def test_empty_notify_returns_empty(session: AsyncSession) -> None:
    result = await derive_implicit_assignee(session, None)
    assert result.user_id is None
    result = await derive_implicit_assignee(session, [])
    assert result.user_id is None
