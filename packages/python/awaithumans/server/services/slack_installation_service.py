"""Slack workspace installation CRUD.

Pure business logic — no HTTP, no Slack API calls. The OAuth route
exchanges the code and then calls this service to persist the result.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import SlackInstallation


async def upsert_installation(
    session: AsyncSession,
    *,
    team_id: str,
    team_name: str | None,
    bot_token: str,
    bot_user_id: str,
    scopes: str,
    enterprise_id: str | None = None,
    installed_by_user_id: str | None = None,
) -> SlackInstallation:
    """Insert a fresh installation row, or update the existing one for this team.

    Reinstalls are common (scopes change, token rotation) — we overwrite
    the row in place rather than versioning.
    """
    existing = await get_installation(session, team_id)
    now = datetime.now(timezone.utc)

    if existing is None:
        row = SlackInstallation(
            team_id=team_id,
            team_name=team_name,
            bot_token=bot_token,
            bot_user_id=bot_user_id,
            scopes=scopes,
            enterprise_id=enterprise_id,
            installed_by_user_id=installed_by_user_id,
            installed_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    existing.team_name = team_name
    existing.bot_token = bot_token
    existing.bot_user_id = bot_user_id
    existing.scopes = scopes
    existing.enterprise_id = enterprise_id
    existing.installed_by_user_id = installed_by_user_id
    existing.updated_at = now
    session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


async def get_installation(session: AsyncSession, team_id: str) -> SlackInstallation | None:
    result = await session.execute(
        select(SlackInstallation).where(SlackInstallation.team_id == team_id)
    )
    return result.scalar_one_or_none()


async def list_installations(session: AsyncSession) -> list[SlackInstallation]:
    result = await session.execute(select(SlackInstallation))
    return list(result.scalars().all())


async def delete_installation(session: AsyncSession, team_id: str) -> bool:
    """Remove an installation. Returns True if a row was deleted."""
    result = await session.execute(
        delete(SlackInstallation).where(SlackInstallation.team_id == team_id)
    )
    await session.commit()
    return result.rowcount > 0
