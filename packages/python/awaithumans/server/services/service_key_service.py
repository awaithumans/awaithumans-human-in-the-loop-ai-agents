"""Service-key CRUD and verification (async).

Public API: create_service_key, verify_service_key, list_service_keys,
            revoke_service_key.
Private helpers: _hash, _ulid.

All functions take an `AsyncSession` (the codebase is async-first; the
mint endpoint and `with_session()` CLI helper both yield AsyncSession).
No FastAPI imports.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from awaithumans.server.db.models import ServiceAPIKey
from awaithumans.server.services.exceptions import ServiceKeyNotFoundError
from awaithumans.utils.constants import (
    SERVICE_KEY_DISPLAY_PREFIX_LENGTH,
    SERVICE_KEY_MAX_NAME_LENGTH,
    SERVICE_KEY_PREFIX,
    SERVICE_KEY_RAW_BYTES,
)

# ── Private helpers ────────────────────────────────────────────────────


def _hash(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw service key string."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _ulid() -> str:
    """Generate a sortable unique ID: 13-hex timestamp-ms + 16-hex random.

    Produces a 29-character lowercase hex string. Not a true ULID (no
    Crockford base32) but satisfies timestamp-ms + secrets.token_hex(8)
    uniqueness — same shape as `_token_id` in embed_token_service.py.
    """
    ts_hex = format(int(time.time() * 1000), "013x")
    rand_hex = secrets.token_hex(8)
    return f"{ts_hex}{rand_hex}"


# ── Public API ─────────────────────────────────────────────────────────


async def create_service_key(session: AsyncSession, *, name: str) -> tuple[str, ServiceAPIKey]:
    """Create a new service key and persist its hash.

    Returns (raw, row). The raw key is shown once, never stored.
    Raises ValueError on empty/oversize name.
    """
    if not name or len(name) > SERVICE_KEY_MAX_NAME_LENGTH:
        raise ValueError(
            f"Service key name must be 1..{SERVICE_KEY_MAX_NAME_LENGTH} chars, got {len(name)}."
        )

    raw = f"{SERVICE_KEY_PREFIX}{secrets.token_hex(SERVICE_KEY_RAW_BYTES)}"
    key_hash = _hash(raw)
    row = ServiceAPIKey(
        id=_ulid(),
        name=name,
        key_hash=key_hash,
        key_prefix=raw[:SERVICE_KEY_DISPLAY_PREFIX_LENGTH],
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return raw, row


async def verify_service_key(session: AsyncSession, raw_key: str) -> ServiceAPIKey:
    """Verify a raw service key and touch last_used_at.

    Raises ServiceKeyNotFoundError on miss OR revoked — same error in
    both cases to avoid leaking row existence to bearer-of-bad-key.
    """
    h = _hash(raw_key)
    result = await session.execute(select(ServiceAPIKey).where(ServiceAPIKey.key_hash == h))
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise ServiceKeyNotFoundError()
    row.last_used_at = datetime.now(UTC)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_service_keys(
    session: AsyncSession, *, include_revoked: bool = False
) -> list[ServiceAPIKey]:
    """Return service keys ordered by created_at."""
    stmt = select(ServiceAPIKey).order_by(ServiceAPIKey.created_at)
    if not include_revoked:
        stmt = stmt.where(ServiceAPIKey.revoked_at.is_(None))  # type: ignore[union-attr]
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def revoke_service_key(session: AsyncSession, key_id: str) -> ServiceAPIKey:
    """Idempotently revoke a service key. Raises ServiceKeyNotFoundError on miss."""
    row = await session.get(ServiceAPIKey, key_id)
    if row is None:
        raise ServiceKeyNotFoundError()
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row
