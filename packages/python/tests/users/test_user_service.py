"""User service — CRUD, validation, uniqueness, password hashing."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.password import verify_password
from awaithumans.server.services.exceptions import (
    UserAlreadyExistsError,
    UserNoAddressError,
    UserNotFoundError,
)
from awaithumans.server.services.user_service import (
    count_users,
    create_user,
    delete_user,
    get_user,
    get_user_by_email,
    get_user_by_slack,
    list_users,
    set_password,
    update_user,
)

# ─── At-least-one-address rule ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_rejects_user_with_no_address(session: AsyncSession) -> None:
    with pytest.raises(UserNoAddressError):
        await create_user(session, display_name="Ghost")


@pytest.mark.asyncio
async def test_create_rejects_partial_slack(session: AsyncSession) -> None:
    """Slack identifiers come in pairs — team alone or user alone is broken."""
    with pytest.raises(UserNoAddressError):
        await create_user(session, slack_team_id="T01")
    with pytest.raises(UserNoAddressError):
        await create_user(session, slack_user_id="U01")


@pytest.mark.asyncio
async def test_create_accepts_email_only(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com")
    assert u.email == "a@example.com"
    assert u.slack_team_id is None


@pytest.mark.asyncio
async def test_create_accepts_slack_only(session: AsyncSession) -> None:
    u = await create_user(session, slack_team_id="T01", slack_user_id="U01")
    assert u.slack_team_id == "T01"
    assert u.email is None


# ─── Uniqueness ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_email_rejected(session: AsyncSession) -> None:
    await create_user(session, email="a@example.com")
    with pytest.raises(UserAlreadyExistsError) as exc:
        await create_user(session, email="a@example.com")
    assert exc.value.conflict == "email"


@pytest.mark.asyncio
async def test_duplicate_slack_pair_rejected(session: AsyncSession) -> None:
    await create_user(session, slack_team_id="T01", slack_user_id="U01")
    with pytest.raises(UserAlreadyExistsError):
        await create_user(session, slack_team_id="T01", slack_user_id="U01")


@pytest.mark.asyncio
async def test_same_slack_user_in_different_team_allowed(session: AsyncSession) -> None:
    """U01 in team A and U01 in team B are different humans."""
    await create_user(session, slack_team_id="T01", slack_user_id="U01")
    u2 = await create_user(session, slack_team_id="T02", slack_user_id="U01")
    assert u2.id != ""


@pytest.mark.asyncio
async def test_two_users_with_null_email_allowed(session: AsyncSession) -> None:
    """Partial unique index only applies when email is non-null."""
    await create_user(session, slack_team_id="T01", slack_user_id="U01")
    await create_user(session, slack_team_id="T01", slack_user_id="U02")
    # Neither has an email — both should coexist.
    users = await list_users(session)
    assert len([u for u in users if u.email is None]) == 2


# ─── Password hashing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_password_is_hashed(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com", password="hunter2a")
    assert u.password_hash is not None
    # Argon2id hashes start with $argon2id$ — quick sanity check.
    assert u.password_hash.startswith("$argon2id$")
    assert u.password_hash != "hunter2a"


@pytest.mark.asyncio
async def test_password_verify(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com", password="hunter2a")
    assert verify_password("hunter2a", u.password_hash or "")
    assert not verify_password("wrong", u.password_hash or "")


@pytest.mark.asyncio
async def test_set_password_updates_hash(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com", password="first8ch")
    original = u.password_hash

    u2 = await set_password(session, u.id, "second8c")
    assert u2.password_hash != original
    assert verify_password("second8c", u2.password_hash or "")


@pytest.mark.asyncio
async def test_set_password_none_clears_hash(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com", password="first8ch")
    assert u.password_hash is not None

    u2 = await set_password(session, u.id, None)
    assert u2.password_hash is None


# ─── Lookups ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_by_id_and_email(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com")
    by_id = await get_user(session, u.id)
    by_email = await get_user_by_email(session, "a@example.com")
    assert by_id is not None and by_email is not None
    assert by_id.id == by_email.id == u.id


@pytest.mark.asyncio
async def test_get_user_by_slack(session: AsyncSession) -> None:
    u = await create_user(session, slack_team_id="T01", slack_user_id="U01")
    found = await get_user_by_slack(session, slack_team_id="T01", slack_user_id="U01")
    assert found is not None
    assert found.id == u.id


@pytest.mark.asyncio
async def test_list_users_filters(session: AsyncSession) -> None:
    await create_user(session, email="a@example.com", role="kyc", pool="ops")
    await create_user(session, email="b@example.com", role="kyc", pool="fraud")
    await create_user(session, email="c@example.com", role="support", pool="ops")

    kyc = await list_users(session, role="kyc")
    assert len(kyc) == 2

    ops = await list_users(session, pool="ops")
    assert len(ops) == 2

    kyc_ops = await list_users(session, role="kyc", pool="ops")
    assert len(kyc_ops) == 1
    assert kyc_ops[0].email == "a@example.com"


# ─── Update + delete ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_user_patches_fields(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com")
    u2 = await update_user(session, u.id, display_name="Alice", role="kyc")
    assert u2.display_name == "Alice"
    assert u2.role == "kyc"
    assert u2.email == "a@example.com"


@pytest.mark.asyncio
async def test_update_rejects_removing_last_address(session: AsyncSession) -> None:
    """You can't update a user into an unreachable state."""
    u = await create_user(session, email="a@example.com")
    with pytest.raises(UserNoAddressError):
        await update_user(session, u.id, email=None)


@pytest.mark.asyncio
async def test_update_unknown_id_raises(session: AsyncSession) -> None:
    with pytest.raises(UserNotFoundError):
        await update_user(session, "does-not-exist", role="kyc")


@pytest.mark.asyncio
async def test_delete_removes_row(session: AsyncSession) -> None:
    u = await create_user(session, email="a@example.com")
    assert await delete_user(session, u.id) is True
    assert await get_user(session, u.id) is None


@pytest.mark.asyncio
async def test_delete_missing_returns_false(session: AsyncSession) -> None:
    assert await delete_user(session, "nope") is False


@pytest.mark.asyncio
async def test_count_users_for_bootstrap(session: AsyncSession) -> None:
    """`/setup` bootstrap uses count_users to decide whether to show the
    first-run form. This test pins that count is zero on empty and grows."""
    assert await count_users(session) == 0
    await create_user(session, email="a@example.com")
    assert await count_users(session) == 1
    await create_user(session, email="b@example.com")
    assert await count_users(session) == 2
