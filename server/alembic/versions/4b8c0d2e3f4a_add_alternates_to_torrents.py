"""add_alternates_to_torrents

Revision ID: 4b8c0d2e3f4a
Revises: 3a7b9c1d2e3f
Create Date: 2026-09-18 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b8c0d2e3f4a"
down_revision: str | None = "3a7b9c1d2e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("torrents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alternates", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("torrents", schema=None) as batch_op:
        batch_op.drop_column("alternates")
