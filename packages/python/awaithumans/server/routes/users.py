"""Admin user directory routes — /api/admin/users CRUD.

Gated by `require_admin` (shared `X-Admin-Token` bearer). PR A3 adds
operator-session auth as an alternative path; until then, ops runs
with the admin token.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.admin_auth import require_admin
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import User
from awaithumans.server.schemas.users import (
    SetPasswordRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from awaithumans.server.services.exceptions import UserNotFoundError
from awaithumans.server.services.user_service import (
    create_user,
    delete_user,
    get_user,
    list_users,
    set_password,
    update_user,
)

router = APIRouter(prefix="/admin/users", tags=["admin"])
logger = logging.getLogger("awaithumans.server.routes.users")


def _to_public(row: User) -> UserResponse:
    """Convert a User model row to its public view. `has_password` is
    derived from the hash presence so callers can tell whether the
    user can log in without seeing the hash itself."""
    return UserResponse(
        id=row.id,
        display_name=row.display_name,
        email=row.email,
        slack_team_id=row.slack_team_id,
        slack_user_id=row.slack_user_id,
        role=row.role,
        access_level=row.access_level,
        pool=row.pool,
        is_operator=row.is_operator,
        has_password=bool(row.password_hash),
        active=row.active,
        last_assigned_at=row.last_assigned_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "",
    response_model=UserResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_user_route(
    body: UserCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    row = await create_user(
        session,
        display_name=body.display_name,
        email=body.email,
        slack_team_id=body.slack_team_id,
        slack_user_id=body.slack_user_id,
        role=body.role,
        access_level=body.access_level,
        pool=body.pool,
        is_operator=body.is_operator,
        password=body.password,
        active=body.active,
    )
    return _to_public(row)


@router.get(
    "",
    response_model=list[UserResponse],
    dependencies=[Depends(require_admin)],
)
async def list_users_route(
    role: str | None = Query(default=None),
    access_level: str | None = Query(default=None),
    pool: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[UserResponse]:
    rows = await list_users(session, role=role, access_level=access_level, pool=pool, active=active)
    return [_to_public(r) for r in rows]


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
async def get_user_route(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    row = await get_user(session, user_id)
    if row is None:
        raise UserNotFoundError(user_id)
    return _to_public(row)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
async def update_user_route(
    user_id: str,
    body: UserUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    # Only send non-null fields to the service — nulls mean "leave alone."
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await update_user(session, user_id, **changes)
    return _to_public(row)


@router.delete(
    "/{user_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_user_route(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not await delete_user(session, user_id):
        raise UserNotFoundError(user_id)
    return Response(status_code=204)


@router.post(
    "/{user_id}/password",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
async def set_password_route(
    user_id: str,
    body: SetPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    row = await set_password(session, user_id, body.password)
    return _to_public(row)


@router.delete(
    "/{user_id}/password",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
async def clear_password_route(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    """Explicitly clear a password — user can no longer log in but can
    still receive tasks via their configured channels. Separate from
    DELETE /users/{id} which hard-deletes the row."""
    row = await set_password(session, user_id, None)
    return _to_public(row)
