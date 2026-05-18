"""User directory CRUD.

The one place that writes to the `users` table. The admin API, the
CLI, the `/setup` bootstrap, and the task router all go through this
module so they can't diverge on validation or hashing.

At-least-one-address rule: enforced here, not in the DB. Partial unique
indexes catch duplicates but can't express "at least one of these two
fields must be non-null" portably across SQLite and Postgres.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.password import hash_password
from awaithumans.server.db.models import User
from awaithumans.server.services.exceptions import (
    LastOperatorError,
    UserAlreadyExistsError,
    UserNoAddressError,
    UserNotFoundError,
)

logger = logging.getLogger("awaithumans.server.services.user")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_addresses(
    *, email: str | None, slack_team_id: str | None, slack_user_id: str | None
) -> None:
    """At least one of {email} or {slack_team_id + slack_user_id}.

    A partial slack address (one of the pair set but not the other) is
    invalid — slack IDs are workspace-scoped and only meaningful together.
    """
    has_email = bool(email)
    has_full_slack = bool(slack_team_id) and bool(slack_user_id)
    has_partial_slack = bool(slack_team_id) != bool(slack_user_id)

    if has_partial_slack:
        raise UserNoAddressError()  # partial slack = broken, same remediation
    if not (has_email or has_full_slack):
        raise UserNoAddressError()


def _infer_conflict(exc: IntegrityError) -> str:
    """Best-effort extraction of which unique constraint fired.

    SQLAlchemy wraps the driver exception; the original message usually
    mentions the index name. Falls back to a generic label so the caller
    always gets a non-empty string.
    """
    msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
    if "ix_users_email_unique" in msg or "email" in msg:
        return "email"
    if "ix_users_slack_unique" in msg or "slack" in msg:
        return "slack identity"
    return "identity"


async def create_user(
    session: AsyncSession,
    *,
    display_name: str | None = None,
    email: str | None = None,
    slack_team_id: str | None = None,
    slack_user_id: str | None = None,
    role: str | None = None,
    access_level: str | None = None,
    pool: str | None = None,
    is_operator: bool = False,
    password: str | None = None,
    active: bool = True,
) -> User:
    """Insert a new user. Raises UserAlreadyExistsError on uniqueness conflict."""
    _validate_addresses(email=email, slack_team_id=slack_team_id, slack_user_id=slack_user_id)

    row = User(
        display_name=display_name,
        email=email,
        slack_team_id=slack_team_id,
        slack_user_id=slack_user_id,
        role=role,
        access_level=access_level,
        pool=pool,
        is_operator=is_operator,
        password_hash=hash_password(password) if password else None,
        active=active,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise UserAlreadyExistsError(_infer_conflict(exc)) from exc
    await session.refresh(row)
    return row


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_slack(
    session: AsyncSession, *, slack_team_id: str, slack_user_id: str
) -> User | None:
    result = await session.execute(
        select(User).where(
            User.slack_team_id == slack_team_id,
            User.slack_user_id == slack_user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_users(
    session: AsyncSession,
    *,
    role: str | None = None,
    access_level: str | None = None,
    pool: str | None = None,
    active: bool | None = None,
) -> list[User]:
    """List users with optional filters. `None` filters are ignored."""
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if access_level is not None:
        stmt = stmt.where(User.access_level == access_level)
    if pool is not None:
        stmt = stmt.where(User.pool == pool)
    if active is not None:
        stmt = stmt.where(User.active == active)
    stmt = stmt.order_by(User.created_at.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_user(
    session: AsyncSession,
    user_id: str,
    **changes: Any,
) -> User:
    """Patch a user. Unknown keys are ignored.

    Pass `password=<str>` to set a new password; it's hashed here
    (service never stores plaintext). Pass `password_hash=<str>`
    directly at your peril — that's an escape hatch for migrations,
    not normal use.

    Refuses to demote (`is_operator=False`) or deactivate
    (`active=False`) the last active operator; see `LastOperatorError`.
    """
    row = await get_user(session, user_id)
    if row is None:
        raise UserNotFoundError(user_id)

    # Pre-flight last-operator guard. Run BEFORE applying the changes
    # so we don't mutate the row and then refuse to commit — leaves
    # the session in a clean state for the caller's next query.
    if row.is_operator and row.active:
        demoting = changes.get("is_operator") is False
        deactivating = changes.get("active") is False
        if demoting or deactivating:
            action = "demote" if demoting else "deactivate"
            await _ensure_not_last_active_operator(session, row.id, action=action)

    if "password" in changes:
        pw = changes.pop("password")
        row.password_hash = hash_password(pw) if pw else None

    updatable = {
        "display_name",
        "email",
        "slack_team_id",
        "slack_user_id",
        "role",
        "access_level",
        "pool",
        "is_operator",
        "active",
        "password_hash",
    }
    for k, v in changes.items():
        if k in updatable:
            setattr(row, k, v)

    _validate_addresses(
        email=row.email,
        slack_team_id=row.slack_team_id,
        slack_user_id=row.slack_user_id,
    )
    row.updated_at = _now()
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise UserAlreadyExistsError(_infer_conflict(exc)) from exc
    await session.refresh(row)
    return row


async def set_password(session: AsyncSession, user_id: str, password: str | None) -> User:
    """Set or clear a user's password.

    Passing None clears the hash — the user can no longer log in but
    can still receive tasks via their configured channels.
    """
    return await update_user(session, user_id, password=password)


async def delete_user(session: AsyncSession, user_id: str) -> bool:
    """Hard delete. Returns True if a row was removed, False if not found.

    Refuses to remove the last active operator (see `LastOperatorError`).
    Without this guard a well-meaning operator could lock themselves
    out of the dashboard."""
    row = await get_user(session, user_id)
    if row is None:
        return False

    if row.is_operator and row.active:
        await _ensure_not_last_active_operator(session, row.id, action="delete")

    result = await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
    return result.rowcount > 0


async def count_users(session: AsyncSession) -> int:
    """Used by the /setup bootstrap path to decide whether the setup
    token is still valid (zero users = first-run, > 0 = setup done)."""
    from sqlalchemy import func

    result = await session.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def _count_active_operators(session: AsyncSession) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(User.is_operator == True)  # noqa: E712 — SQLAlchemy idiom
        .where(User.active == True)  # noqa: E712
    )
    return int(result.scalar_one())


async def _ensure_not_last_active_operator(
    session: AsyncSession, user_id: str, *, action: str
) -> None:
    """Raise `LastOperatorError` if removing/demoting this user would
    leave zero active operators. Used by `delete_user` + `update_user`
    to prevent accidental full lockout."""
    active_ops = await _count_active_operators(session)
    if active_ops <= 1:
        raise LastOperatorError(action)
