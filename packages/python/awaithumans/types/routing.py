"""Routing types — assignment targets and human identity."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PoolAssignment(BaseModel):
    pool: str


class RoleAssignment(BaseModel):
    role: str
    access_level: str | None = None


class UserAssignment(BaseModel):
    user_id: str


class MarketplaceAssignment(BaseModel):
    marketplace: Literal[True] = True


AssignTo = (
    str  # email — direct assignment
    | list[str]  # multiple emails — first to claim
    | PoolAssignment  # named pool
    | RoleAssignment  # role-based (optionally with access level)
    | UserAssignment  # internal user ID
    | MarketplaceAssignment  # reserved for Phase 3
)


class HumanIdentity(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    roles: list[str] | None = None
    access_level: str | None = None
