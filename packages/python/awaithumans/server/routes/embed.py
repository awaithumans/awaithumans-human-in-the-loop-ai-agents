"""POST /api/embed/tokens — mint short-lived JWTs for iframe embedding."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_session
from awaithumans.server.schemas.embed import EmbedTokenRequest, EmbedTokenResponse
from awaithumans.server.services.embed_token_service import (
    origin_in_allowlist,
    parse_origin_allowlist,
    sign_embed_token,
)
from awaithumans.server.services.exceptions import (
    EmbedOriginNotAllowedError,
    ServiceKeyNotFoundError,
)
from awaithumans.server.services.service_key_service import verify_service_key
from awaithumans.server.services.task_service import get_task
from awaithumans.utils.constants import EMBED_TOKEN_DEFAULT_TTL_SECONDS

logger = logging.getLogger("awaithumans.server.embed")
router = APIRouter()


async def require_service_key(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Verify Authorization: Bearer ah_sk_... — return key name (or 'self-host')."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ServiceKeyNotFoundError()
    raw = authorization.split(" ", 1)[1].strip()
    if not raw.startswith("ah_sk_"):
        raise ServiceKeyNotFoundError()

    # Self-host fallback: a single key in the env var when no DB rows exist.
    if settings.SERVICE_API_KEY and raw == settings.SERVICE_API_KEY:
        return "self-host"

    row = await verify_service_key(session, raw)
    return row.name


@router.post("/api/embed/tokens", response_model=EmbedTokenResponse)
async def mint_embed_token(
    body: EmbedTokenRequest,
    request: Request,
    _key_name: str = Depends(require_service_key),
    session: AsyncSession = Depends(get_session),
) -> EmbedTokenResponse:
    if not settings.EMBED_SIGNING_SECRET:
        raise HTTPException(status_code=503, detail="embed_signing_not_configured")

    allowlist = parse_origin_allowlist(settings.EMBED_PARENT_ORIGINS)
    if not origin_in_allowlist(body.parent_origin, allowlist):
        raise EmbedOriginNotAllowedError(origin=body.parent_origin)

    task = await get_task(session, body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")

    token, exp = sign_embed_token(
        secret=settings.EMBED_SIGNING_SECRET,
        task_id=body.task_id,
        sub=body.sub,
        kind="end_user",
        parent_origin=body.parent_origin,
        ttl_seconds=body.ttl_seconds or EMBED_TOKEN_DEFAULT_TTL_SECONDS,
    )

    base = str(request.base_url).rstrip("/")
    return EmbedTokenResponse(
        embed_token=token,
        embed_url=f"{base}/embed?id={body.task_id}#token={token}",
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
    )
