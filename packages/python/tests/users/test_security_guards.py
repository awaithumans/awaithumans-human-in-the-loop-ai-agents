"""Security audit regression tests.

Pins:
  - last-operator delete / demote / deactivate refused
  - login timing-equalized (unknown user takes ~same CPU as known)
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.password import dummy_verify, hash_password, verify_password
from awaithumans.server.services.exceptions import LastOperatorError
from awaithumans.server.services.user_service import (
    create_user,
    delete_user,
    update_user,
)

# ─── Last-operator guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_last_operator_refused(session: AsyncSession) -> None:
    op = await create_user(session, email="op@example.com", is_operator=True, password="hunter2a")

    with pytest.raises(LastOperatorError) as exc:
        await delete_user(session, op.id)
    assert exc.value.action == "delete"


@pytest.mark.asyncio
async def test_delete_operator_ok_when_another_exists(session: AsyncSession) -> None:
    op1 = await create_user(session, email="op1@example.com", is_operator=True, password="pass1234")
    await create_user(session, email="op2@example.com", is_operator=True, password="pass2345")

    ok = await delete_user(session, op1.id)
    assert ok is True


@pytest.mark.asyncio
async def test_delete_non_operator_always_ok(session: AsyncSession) -> None:
    """Regular user delete is unaffected by the operator guard."""
    await create_user(session, email="op@example.com", is_operator=True, password="pass")
    reg = await create_user(session, email="reg@example.com", role="reviewer")

    assert await delete_user(session, reg.id) is True


@pytest.mark.asyncio
async def test_demote_last_operator_refused(session: AsyncSession) -> None:
    op = await create_user(session, email="op@example.com", is_operator=True, password="hunter2a")

    with pytest.raises(LastOperatorError) as exc:
        await update_user(session, op.id, is_operator=False)
    assert exc.value.action == "demote"


@pytest.mark.asyncio
async def test_deactivate_last_operator_refused(session: AsyncSession) -> None:
    op = await create_user(session, email="op@example.com", is_operator=True, password="hunter2a")

    with pytest.raises(LastOperatorError) as exc:
        await update_user(session, op.id, active=False)
    assert exc.value.action == "deactivate"


@pytest.mark.asyncio
async def test_demote_operator_ok_when_another_exists(session: AsyncSession) -> None:
    op1 = await create_user(session, email="op1@example.com", is_operator=True, password="pass1234")
    await create_user(session, email="op2@example.com", is_operator=True, password="pass2345")

    updated = await update_user(session, op1.id, is_operator=False)
    assert updated.is_operator is False


@pytest.mark.asyncio
async def test_last_operator_guard_ignores_inactive_operators(
    session: AsyncSession,
) -> None:
    """An inactive operator doesn't count toward the "at least one"
    requirement — deleting the only active operator is still refused
    even if inactive operator rows exist."""
    # Create one active operator + one inactive operator.
    active = await create_user(
        session, email="active@example.com", is_operator=True, password="pass1234"
    )
    await create_user(
        session,
        email="inactive@example.com",
        is_operator=True,
        password="pass2345",
        active=False,
    )

    with pytest.raises(LastOperatorError):
        await delete_user(session, active.id)


# ─── Login timing equalization ────────────────────────────────────────


def test_dummy_verify_takes_comparable_time_to_real_verify() -> None:
    """dummy_verify must spend ~the same wall-clock time as a real
    argon2 verify; otherwise the login unknown-user path leaks
    account existence via timing."""
    real_hash = hash_password("a-real-password-abc")

    # Warm up argon2 so first-call overhead doesn't skew results.
    verify_password("a-real-password-abc", real_hash)
    dummy_verify("anything")

    t0 = time.perf_counter()
    for _ in range(3):
        verify_password("wrong-password", real_hash)
    real_duration = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(3):
        dummy_verify("wrong-password")
    dummy_duration = time.perf_counter() - t0

    # Allow 2x tolerance — CI machines are noisy. The point is that
    # dummy_verify is not *orders of magnitude* faster than the real
    # path (which is what an attacker would exploit).
    ratio = max(real_duration, dummy_duration) / max(min(real_duration, dummy_duration), 1e-6)
    assert ratio < 2.0, (
        f"dummy_verify timing drift: real={real_duration:.3f}s, "
        f"dummy={dummy_duration:.3f}s (ratio={ratio:.2f})"
    )
