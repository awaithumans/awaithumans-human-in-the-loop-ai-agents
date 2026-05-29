"""add task_metadata to tasks

Revision ID: fa6b843c3051
Revises: 5db5d7ba1124
Create Date: 2026-05-28 21:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa6b843c3051"
down_revision: str | None = "5db5d7ba1124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("task_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("task_metadata")
