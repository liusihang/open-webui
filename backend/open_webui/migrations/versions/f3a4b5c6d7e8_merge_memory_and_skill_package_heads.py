"""merge memory and skill package migration heads

Revision ID: f3a4b5c6d7e8
Revises: 42e2978c7933, e2f3a4b5c7
Create Date: 2026-07-07 01:32:00.000000

"""

from collections.abc import Sequence


revision: str = 'f3a4b5c6d7e8'
down_revision: tuple[str, str] = ('42e2978c7933', 'e2f3a4b5c7')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
