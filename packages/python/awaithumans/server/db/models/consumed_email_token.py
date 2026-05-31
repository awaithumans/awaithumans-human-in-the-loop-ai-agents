"""Single-use marker for magic-link tokens.

Without this row, a magic-link URL is replayable for as long as it
hasn't expired (default 24h). Forwarded emails, Outlook SafeLinks
fetches, mailbox compromise, mail-server logs — every leak vector
becomes a multi-hour replay window. The presence of a row keyed on
the token's `jti` is the authoritative "this token has been used"
signal; the unique PK makes the consume operation race-safe under
concurrent POSTs to the same URL.

Rows accumulate with no automatic GC — operators can prune by
`consumed_at < now - MAGIC_LINK_MAX_AGE_SECONDS` since any token
older than its TTL is rejected by HMAC verify anyway. We don't add
the prune to the v1 timeout scheduler because the table is small
(one row per email completion) and the read pattern is `SELECT 1
FROM consumed_email_tokens WHERE jti = ?` — indexed by PK, doesn't
slow down with growth."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from awaithumans.server.db.models.base import tz_timestamp_column, utc_now


class ConsumedEmailToken(SQLModel, table=True):
    """One row per magic-link token that has been redeemed."""

    __tablename__ = "consumed_email_tokens"

    jti: str = Field(primary_key=True, max_length=64)
    consumed_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
