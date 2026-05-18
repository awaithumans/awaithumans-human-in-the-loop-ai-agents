"""System status schema — safe-to-display diagnostic data."""

from __future__ import annotations

from pydantic import BaseModel


class SystemStatus(BaseModel):
    """Operator-facing server status. No secrets, no keys, no passwords.

    Everything here is either "is X configured" (bool) or "what mode
    are we in" (enum-like string). The exact values/paths/tokens stay
    server-side.
    """

    version: str
    environment: str
    public_url: str

    # Auth + crypto
    auth_enabled: bool
    payload_encryption_enabled: bool
    admin_token_enabled: bool

    # Channels — each value is a mode the operator picked. Not the keys.
    slack_mode: str  # "off" | "single-workspace" | "multi-workspace"
    email_transport: str | None  # "resend" | "smtp" | "logging" | "noop" | None
    email_from: str | None
