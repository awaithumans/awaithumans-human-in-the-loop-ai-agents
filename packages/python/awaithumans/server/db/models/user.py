"""User model — one row per human reachable by the system.

A "user" here is a person who can either receive tasks (via Slack,
email, or by picking them up on the dashboard) or log into the
dashboard to manage tasks / other users.

**At least one delivery address must be set** — either `email` or
the `(slack_team_id, slack_user_id)` pair. Rows with neither aren't
reachable by any channel and are useless for routing. The service
layer enforces this; the DB has partial unique indexes on each
address so duplicates can't be created.

**Operator vs regular user.** `is_operator=True` grants dashboard
admin access (user management, viewing all tasks). Any other role
is a routing label — free-form by design because every business
models its hierarchy differently.

**Login.** `password_hash` is nullable because not every user logs
in. Slack-only users never see the dashboard. Operators always have
a password. Regular dashboard users opt in by setting one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now


class User(SQLModel, table=True):
    """A human in the user directory."""

    __tablename__ = "users"

    id: str = Field(default_factory=new_id, primary_key=True)
    display_name: str | None = Field(default=None)

    # ── Delivery addresses — at least one must be set ────────────────
    email: str | None = Field(default=None)
    slack_team_id: str | None = Field(default=None)
    slack_user_id: str | None = Field(default=None)

    # ── Routing attributes (free-form strings) ──────────────────────
    role: str | None = Field(default=None)
    access_level: str | None = Field(default=None)
    pool: str | None = Field(default=None)

    # ── Auth ─────────────────────────────────────────────────────────
    is_operator: bool = Field(default=False)
    password_hash: str | None = Field(default=None)

    # ── State ────────────────────────────────────────────────────────
    active: bool = Field(default=True)
    # Least-recently-assigned scheduling key. Null sorts first so
    # new hires get picked ahead of veterans (correct default — their
    # queue is empty). Updated transactionally when the router picks
    # this user.
    last_assigned_at: datetime | None = Field(
        default=None,
        sa_column=tz_timestamp_column(nullable=True),
    )

    # ── Timestamps ───────────────────────────────────────────────────
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )

    __table_args__ = (
        # Email unique across rows that actually have one.
        Index(
            "ix_users_email_unique",
            "email",
            unique=True,
            sqlite_where=text("email IS NOT NULL"),
            postgresql_where=text("email IS NOT NULL"),
        ),
        # Slack identity is workspace-scoped (U123 in team A != team B).
        # Unique on the pair, only when both sides are set.
        Index(
            "ix_users_slack_unique",
            "slack_team_id",
            "slack_user_id",
            unique=True,
            sqlite_where=text("slack_user_id IS NOT NULL"),
            postgresql_where=text("slack_user_id IS NOT NULL"),
        ),
    )
