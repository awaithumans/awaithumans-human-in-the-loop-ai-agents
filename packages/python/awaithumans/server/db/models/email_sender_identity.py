"""Email sender identity — one row per configured sender.

A sender identity pairs a `From:` address with transport credentials.
Developers can have multiple identities (e.g. "prod" uses Resend with
notifications@acme.com; "staging" uses SMTP to a dev inbox). Tasks
pick an identity via `notify=["email+acme-prod:alice@..."]`.

`transport_config` is the raw JSON string of transport-specific settings
(API keys, SMTP host/user/password, etc.) — stored encrypted at rest
via EncryptedString. A DB dump alone reveals no provider credentials.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from awaithumans.server.core.encryption import EncryptedString
from awaithumans.server.db.models.base import tz_timestamp_column, utc_now


class EmailSenderIdentity(SQLModel, table=True):
    """One configured email sender identity."""

    __tablename__ = "email_sender_identities"

    # Slug primary key — user-friendly, used in `notify` strings.
    # Example: "acme-prod", "staging", "support-team".
    id: str = Field(primary_key=True)

    display_name: str

    # From: header components.
    from_email: str
    from_name: str | None = Field(default=None)

    # Reply-To: override (optional; defaults to from_email on the wire).
    reply_to: str | None = Field(default=None)

    # Which transport backend to use: "resend" | "smtp" | "logging" | "noop".
    # Values match the keys in the transport factory.
    transport: str

    # JSON blob of transport-specific config (SMTP creds, API keys).
    # ENCRYPTED at rest — service code reads and writes plaintext JSON,
    # the binding layer encrypts on INSERT/UPDATE and decrypts on SELECT.
    transport_config: str = Field(sa_column=Column(EncryptedString, nullable=False))

    # Set to True after a test email has successfully delivered.
    # Unverified identities can still be used (operator's choice) but
    # the dashboard will flag them.
    verified: bool = Field(default=False)
    verified_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
