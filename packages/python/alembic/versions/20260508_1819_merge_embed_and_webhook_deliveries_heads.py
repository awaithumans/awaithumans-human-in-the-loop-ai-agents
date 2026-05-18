"""merge embed and webhook deliveries heads

Revision ID: 5db5d7ba1124
Revises: e5081f86ee1b, 61101cef342e
Create Date: 2026-05-08 18:19:42.198159

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "5db5d7ba1124"
down_revision: str | None = ("e5081f86ee1b", "61101cef342e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
