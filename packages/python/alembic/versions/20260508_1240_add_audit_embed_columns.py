"""add audit embed columns

Revision ID: 61101cef342e
Revises: f545073ea8e0
Create Date: 2026-05-08 12:40:45.576934

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "61101cef342e"
down_revision: str | None = "f545073ea8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add embed_sub and embed_jti to audit_entries.

    The existing `channel` column on audit_entries already discriminates
    'dashboard' | 'slack' | 'email' | 'webhook'; we extend it to accept
    a new 'embed' value via the application layer (no schema constraint
    needed). The two new columns capture the partner-side identifiers
    that come in on embed-token JWTs (`sub` and `jti` claims) so the
    operator dashboard can attribute who completed each task and which
    token issued the action — useful for incident triage on a leaked
    token.
    """
    op.add_column(
        "audit_entries",
        sa.Column("embed_sub", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "audit_entries",
        sa.Column("embed_jti", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "audit_entries_embed_jti_idx",
        "audit_entries",
        ["embed_jti"],
        unique=False,
        postgresql_where=sa.text("embed_jti IS NOT NULL"),
        sqlite_where=sa.text("embed_jti IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("audit_entries_embed_jti_idx", table_name="audit_entries")
    op.drop_column("audit_entries", "embed_jti")
    op.drop_column("audit_entries", "embed_sub")
