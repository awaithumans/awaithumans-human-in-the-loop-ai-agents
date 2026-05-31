"""Reference to a Slack message we posted for a task.

When a task is created with `notify=["slack:..."]` we post to one or
more Slack DMs / channels. Each posted message gets a row here so we
can come back later (after the task transitions to a terminal state)
and update the message via `chat.update`. Without this table, the
original "Approve / Reject" message in Slack stays interactive
forever — operators reading their DMs days later would still see an
"open" task that's been done.

One task can have multiple rows: a DM to the assignee plus a #channel
broadcast, or an `notify=` list with several entries. We update all
of them on completion / cancel / timeout.

Rows are best-effort: we never fail task creation on a missed insert,
and we never fail completion on a missed update. Slack outages affect
the cosmetic message, not the task lifecycle.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from awaithumans.server.db.models.base import new_id, tz_timestamp_column, utc_now


class SlackTaskMessage(SQLModel, table=True):
    """One row per Slack message we posted for a task.

    `channel` and `ts` are the (chat) coordinates Slack expects on
    `chat.update`. `team_id` lets us pick the right OAuth client when
    we run the update — multi-workspace setups have a different bot
    token per workspace.
    """

    __tablename__ = "slack_task_messages"

    id: str = Field(default_factory=new_id, primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True)
    channel: str = Field(max_length=64)
    ts: str = Field(max_length=64)
    # Null when the static-token (default workspace) client posted —
    # there's only one workspace by construction so we don't need to
    # pin one. Set when an OAuth-installed workspace posted.
    team_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=tz_timestamp_column(),
    )
