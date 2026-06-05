"""add demo_records.

Revision ID: 20260604_1200
Revises: 8b4ed1c70a5f
Create Date: 2026-06-04 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_1200"
down_revision = "8b4ed1c70a5f"
branch_labels = None
depends_on = None

_DEMO_STATUS_VALUES = (
    "extracting",
    "routing",
    "awaiting_claim",
    "claimed",
    "partially_done",
    "review_complete",
    "email_sent",
    "email_failed",
    "routing_failed",
    "fallback_sent",
)


def upgrade() -> None:
    demo_status = sa.Enum(*_DEMO_STATUS_VALUES, name="demostatus", native_enum=False)
    op.create_table(
        "demo_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("email_domain", sa.String(), nullable=False),
        sa.Column("ip_hash", sa.String(), nullable=False),
        sa.Column("is_hot_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("preset_key", sa.String(), nullable=False),
        sa.Column("ai_result", sa.JSON(), nullable=True),
        sa.Column("field_confidences", sa.JSON(), nullable=False),
        sa.Column("pending_field_names", sa.JSON(), nullable=False),
        sa.Column("field_corrections", sa.JSON(), nullable=False),
        sa.Column("awaitverify_task_id", sa.String(), nullable=True),
        sa.Column("status", demo_status, nullable=False, server_default="extracting"),
        sa.Column("cost_estimate_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("email_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_demo_records_email", "demo_records", ["email"])
    op.create_index("ix_demo_records_email_domain", "demo_records", ["email_domain"])
    op.create_index("ix_demo_records_ip_hash", "demo_records", ["ip_hash"])
    op.create_index(
        "ix_demo_records_awaitverify_task_id",
        "demo_records",
        ["awaitverify_task_id"],
    )
    op.create_index("ix_demo_records_email_created", "demo_records", ["email", "created_at"])
    op.create_index("ix_demo_records_ip_created", "demo_records", ["ip_hash", "created_at"])
    op.create_index(
        "ix_demo_records_created_status",
        "demo_records",
        ["created_at", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_demo_records_created_status", table_name="demo_records")
    op.drop_index("ix_demo_records_ip_created", table_name="demo_records")
    op.drop_index("ix_demo_records_email_created", table_name="demo_records")
    op.drop_index("ix_demo_records_awaitverify_task_id", table_name="demo_records")
    op.drop_index("ix_demo_records_ip_hash", table_name="demo_records")
    op.drop_index("ix_demo_records_email_domain", table_name="demo_records")
    op.drop_index("ix_demo_records_email", table_name="demo_records")
    op.drop_table("demo_records")
