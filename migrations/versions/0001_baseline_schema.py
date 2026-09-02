"""baseline schema

Ports uber_clone's src/main/resources/db/migration/V1__baseline_schema.sql column-for-column.

Revision ID: 0001
Revises:
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("avg_rating", sa.Double(), nullable=True),
    )

    op.create_table(
        "drivers",
        sa.Column("user_id", sa.String(255), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("vehicle_type", sa.String(255), nullable=False),
        sa.Column("license_plate", sa.String(255), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("avg_rating", sa.Double(), nullable=True),
    )

    op.create_table(
        "rides",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("rider_id", sa.String(255), nullable=False),
        sa.Column("driver_id", sa.String(255), nullable=True),
        sa.Column("pickup_lat", sa.Double(), nullable=False),
        sa.Column("pickup_lng", sa.Double(), nullable=False),
        sa.Column("dropoff_lat", sa.Double(), nullable=False),
        sa.Column("dropoff_lng", sa.Double(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("estimated_fare", sa.Numeric(19, 2), nullable=True),
        sa.Column("fare", sa.Numeric(19, 2), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "ratings",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("ride_id", sa.String(255), nullable=False),
        sa.Column("from_user_id", sa.String(255), nullable=False),
        sa.Column("to_user_id", sa.String(255), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ride_id", "from_user_id", name="uq_ratings_ride_from"),
    )


def downgrade() -> None:
    op.drop_table("ratings")
    op.drop_table("rides")
    op.drop_table("drivers")
    op.drop_table("users")
