"""Slack workspace installation record.

One row per Slack workspace that has installed the awaithumans app. For
self-hosted deployments using AWAITHUMANS_SLACK_BOT_TOKEN in env, this
table is empty — the static token is used instead. For multi-workspace
deployments, every OAuth install upserts a row here keyed by team_id.

`bot_token` is encrypted at rest via EncryptedString (AES-256-GCM). A
DB dump alone is not enough to compromise a workspace — the attacker
also needs AWAITHUMANS_PAYLOAD_KEY. See server/core/encryption.py.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from awaithumans.server.core.encryption import EncryptedString
from awaithumans.server.db.models.base import tz_timestamp_column, utc_now


class SlackInstallation(SQLModel, table=True):
    """One Slack workspace's installation of the awaithumans bot."""

    __tablename__ = "slack_installations"

    # Slack's workspace ID (`T...`). Primary key — a workspace can only
    # have one active installation at a time; reinstalling upserts.
    team_id: str = Field(primary_key=True)
    team_name: str | None = Field(default=None)

    # The bot user awaithumans runs as in this workspace.
    bot_user_id: str

    # Encrypted at rest via EncryptedString. Service code sees plain
    # xoxb-... tokens; the SQLAlchemy binding layer handles the crypto.
    bot_token: str = Field(sa_column=Column(EncryptedString, nullable=False))

    # Comma-separated. Stored as given by Slack so we can detect drift.
    scopes: str

    # Enterprise Grid: parent org if applicable.
    enterprise_id: str | None = Field(default=None, index=True)

    # Who kicked off the install — useful for support + audit.
    installed_by_user_id: str | None = Field(default=None)

    installed_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
