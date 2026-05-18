"""Slack API request/response schemas.

`bot_token` is NEVER part of a response model — it's stored encrypted
and only read server-side by notifier.py. The public shape only
carries enough to recognise which workspace an installation is.
"""

from __future__ import annotations

from pydantic import BaseModel


class SlackInstallationResponse(BaseModel):
    team_id: str
    team_name: str | None
    bot_user_id: str
    scopes: str
    enterprise_id: str | None
    installed_by_user_id: str | None
    installed_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class SlackStaticWorkspaceResponse(BaseModel):
    """The workspace behind the env-configured `SLACK_BOT_TOKEN`.

    Static-token mode skips the DB-backed `slack_installations` table
    entirely — the token lives in an env var, not a row. The dashboard
    still wants to confirm which team that token belongs to and let
    operators use the member picker, so we surface team_id +
    team_name via Slack's `auth.test` API on demand. Read-only —
    uninstalling means dropping the env var, not calling the API."""

    team_id: str
    team_name: str | None
    bot_user_id: str | None


class SlackMemberResponse(BaseModel):
    """A member of a Slack workspace — enough to render a picker.

    We deliberately don't forward the full Slack profile; dashboard
    only needs enough to label the row and record the stable ID."""

    id: str  # Slack user ID (U… / W…)
    name: str  # @handle ("alice")
    real_name: str | None
    display_name: str | None
    is_admin: bool
