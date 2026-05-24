"""Unit tests for `link_slack_identity_by_email` (#144).

Covers every branch of the auto-link helper: happy path, idempotent
no-op re-link, refusal when the user already has a different Slack
identity bound, refusal when the email matches no directory user.
"""

from __future__ import annotations

import secrets
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.db.models import User  # noqa: F401  (register for create_all)
from awaithumans.server.services.user_service import (
    create_user,
    get_user_by_email,
    link_slack_identity_by_email,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Fresh in-memory SQLite per test."""
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()
    # File-based SQLite — async sqlite with shared in-memory across
    # connections needs a URI dance; tempfile is simpler.
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_link_writes_slack_identity_on_unbound_user(
    session: AsyncSession,
) -> None:
    """The classic happy path: first Slack click by an operator whose
    email matches a directory user with no Slack identity yet."""
    await create_user(
        session,
        email="alex@example.com",
        display_name="Alex",
        is_operator=True,
        password="correct-horse-battery-staple",
    )

    linked = await link_slack_identity_by_email(
        session,
        email="alex@example.com",
        slack_team_id="T_ACME",
        slack_user_id="U_ALEX",
    )

    assert linked is not None
    assert linked.email == "alex@example.com"
    assert linked.slack_team_id == "T_ACME"
    assert linked.slack_user_id == "U_ALEX"


@pytest.mark.asyncio
async def test_auto_link_returns_none_for_unknown_email(
    session: AsyncSession,
) -> None:
    """No directory user with this email → no link, no error."""
    linked = await link_slack_identity_by_email(
        session,
        email="nobody@example.com",
        slack_team_id="T_ACME",
        slack_user_id="U_NOBODY",
    )
    assert linked is None


@pytest.mark.asyncio
async def test_auto_link_refuses_when_slack_identity_already_bound_differently(
    session: AsyncSession,
) -> None:
    """If the matched user already has a DIFFERENT slack_user_id set,
    never silently rebind — operator must explicitly change it."""
    user = await create_user(
        session,
        email="alex@example.com",
        display_name="Alex",
        is_operator=True,
        password="correct-horse-battery-staple",
    )
    # Pre-seed a Slack identity (as if a previous link or a manual
    # set via the dashboard had bound them to a different account).
    user.slack_team_id = "T_ACME"
    user.slack_user_id = "U_ORIGINAL"
    await session.commit()

    linked = await link_slack_identity_by_email(
        session,
        email="alex@example.com",
        slack_team_id="T_ACME",
        slack_user_id="U_HIJACKER",
    )
    assert linked is None

    # Original binding must be untouched.
    fresh = await get_user_by_email(session, "alex@example.com")
    assert fresh is not None
    assert fresh.slack_user_id == "U_ORIGINAL"


@pytest.mark.asyncio
async def test_auto_link_is_idempotent_when_already_correctly_bound(
    session: AsyncSession,
) -> None:
    """Re-linking the SAME identity to the SAME user is a no-op success.
    Useful when a race between two first-clicks both reach the linker —
    one wins the atomic update, the other gets the already-bound row
    back and proceeds normally."""
    user = await create_user(
        session,
        email="alex@example.com",
        display_name="Alex",
        is_operator=True,
        password="correct-horse-battery-staple",
    )
    user.slack_team_id = "T_ACME"
    user.slack_user_id = "U_ALEX"
    await session.commit()

    linked = await link_slack_identity_by_email(
        session,
        email="alex@example.com",
        slack_team_id="T_ACME",
        slack_user_id="U_ALEX",
    )
    assert linked is not None
    assert linked.id == user.id
    assert linked.slack_user_id == "U_ALEX"
