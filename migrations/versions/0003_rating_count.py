"""rating count

Ports uber_clone's V3__rating_count.sql, backfill included — a no-op on a fresh database but
kept for parity/documentation with the Kotlin migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("drivers", sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"))

    op.execute(
        """
        UPDATE users u
        SET rating_count = (SELECT COUNT(*) FROM ratings r WHERE r.to_user_id = u.id)
        """
    )
    op.execute(
        """
        UPDATE drivers d
        SET rating_count = (SELECT COUNT(*) FROM ratings r WHERE r.to_user_id = d.user_id)
        """
    )


def downgrade() -> None:
    op.drop_column("drivers", "rating_count")
    op.drop_column("users", "rating_count")
