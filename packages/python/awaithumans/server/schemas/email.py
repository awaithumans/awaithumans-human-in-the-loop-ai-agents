"""Email channel API request/response schemas.

Split out from routes/email.py so routes carry handlers only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from awaithumans.utils.constants import EMAIL_IDENTITY_ID_MAX_LENGTH


class IdentityCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=EMAIL_IDENTITY_ID_MAX_LENGTH)
    display_name: str
    from_email: str
    from_name: str | None = None
    reply_to: str | None = None
    transport: str  # "resend" | "smtp" | "logging" | "noop" | "file"
    transport_config: dict[str, Any]  # kind-specific (api_key, host, port, dir, ...)


class IdentityResponse(BaseModel):
    """Public view of an identity — NEVER includes transport_config.

    The decrypted config lives in the DB + service layer only. Even admins
    can't read it back via the API — if someone needs to rotate a key, they
    POST a new identity config (upsert). This prevents an attacker who
    steals the admin token from exfiltrating provider credentials en masse.
    """

    id: str
    display_name: str
    from_email: str
    from_name: str | None
    reply_to: str | None
    transport: str
    verified: bool
    verified_at: str | None

    model_config = {"from_attributes": True}
