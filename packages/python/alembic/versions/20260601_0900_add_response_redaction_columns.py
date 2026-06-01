"""add response-redaction columns to tasks

Revision ID: 8b4ed1c70a5f
Revises: 3c2a91e4f8d1
Create Date: 2026-06-01 09:00:00.000000

Two new columns on ``tasks`` for the AwaitVerify response-redaction
flow:

  * ``redact_response_after_submit BOOL NOT NULL DEFAULT FALSE`` —
    captured from the create request. Default False preserves
    existing behavior for non-AwaitVerify callers.
  * ``response_redacted_at TIMESTAMP WITH TIME ZONE NULL`` — set by
    the webhook dispatcher when the callback URL returns 2xx and
    the flag above is True. The dashboard reads this column to
    switch between the structured read-back and a "Response
    delivered" placeholder.

Both columns are additive — existing rows backfill to safe defaults
(False / null), no data migration needed.

Uses ``DateTime(timezone=True)`` for the timestamp so it lands as
``TIMESTAMP WITH TIME ZONE`` on Postgres natively. PR #164's
runtime patch handles pre-PR-6 columns; new columns get the right
shape at creation time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b4ed1c70a5f"
down_revision: str | None = "3c2a91e4f8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "redact_response_after_submit",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "response_redacted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("response_redacted_at")
        batch_op.drop_column("redact_response_after_submit")
