"""create service_api_keys

Revision ID: f545073ea8e0
Revises: 96a2f32bb179
Create Date: 2026-05-08 05:10:14.987723

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f545073ea8e0"
down_revision: str | None = "96a2f32bb179"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "service_api_keys_tenant_idx",
        "service_api_keys",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("service_api_keys_tenant_idx", table_name="service_api_keys")
    op.drop_table("service_api_keys")
