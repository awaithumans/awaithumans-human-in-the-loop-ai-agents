"""add initial_response to tasks

Revision ID: 3c2a91e4f8d1
Revises: fa6b843c3051
Create Date: 2026-05-30 22:00:00.000000

Adds the ``initial_response`` JSON column to the ``tasks`` table for
AwaitVerify Flow A / Flow B pre-fill. Pre-computed extraction values
(matching ``response_schema``) land here and the dashboard mounts the
form with those values pre-populated, so the reviewer verifies rather
than re-types.

Nullable: most non-AwaitVerify tasks (plain await_human callers,
pure-human review) won't carry a pre-computed extraction. New column,
new field — no backfill needed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c2a91e4f8d1"
down_revision: str | None = "fa6b843c3051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("initial_response", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("initial_response")
