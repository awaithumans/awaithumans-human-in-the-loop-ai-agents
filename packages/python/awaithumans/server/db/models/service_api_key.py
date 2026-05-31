"""Partner-held API key for minting embed tokens.

A service key is a static bearer secret (`ah_sk_<32-hex>`) that a
partner's *backend* sends with `POST /api/embed/tokens` to authenticate
its right to mint short-lived embed JWTs for its own tasks. See
docs/superpowers/specs/2026-05-06-dashboard-embedding-design.md §5.3.

Stored as SHA-256 of the raw key. The raw value is shown to the operator
exactly once at creation time — the DB only ever holds the hash, so a
DB compromise can't be turned into mint-endpoint authority.

`key_prefix` is the first 12 chars of the raw key (`ah_sk_` + 6 chars).
We surface the prefix in audit log lines and in the operator's
service-key listing UI so the partner can identify which key did what
without exposing the rest of the secret. `last_used_at` is touched on
every successful verify; useful for spotting unused keys to revoke.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from awaithumans.server.db.models.base import tz_timestamp_column


class ServiceAPIKey(SQLModel, table=True):
    """One row per `ah_sk_*` key that can mint embed tokens."""

    __tablename__ = "service_api_keys"

    id: str = Field(primary_key=True, max_length=36)
    name: str = Field(max_length=80)
    key_hash: str = Field(unique=True, max_length=64)
    key_prefix: str = Field(max_length=12)
    # Already tz-aware in the original alembic migration (20260508_0510),
    # so prod deploys don't need the startup patch to repair this table.
    # We still declare timezone=True at the model level for consistency:
    # tests using `metadata.create_all` then produce tz-aware columns
    # too, matching what the migration created in prod.
    created_at: datetime = Field(sa_column=tz_timestamp_column())
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )
