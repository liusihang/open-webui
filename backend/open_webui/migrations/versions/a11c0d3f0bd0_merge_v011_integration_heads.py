"""Merge the custom and official v0.11 migration branches.

Revision ID: a11c0d3f0bd0
Revises: c0d3b4a5e6f7, f0bd01a18a3d
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = 'a11c0d3f0bd0'
down_revision: tuple[str, str] = ('c0d3b4a5e6f7', 'f0bd01a18a3d')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
