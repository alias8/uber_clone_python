"""ride indexes

Ports uber_clone's V2__ride_indexes.sql.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("idx_rides_rider_requested", "rides", ["rider_id", "requested_at"])
    op.create_index("idx_rides_driver_requested", "rides", ["driver_id", "requested_at"])
    op.create_index("idx_rides_driver_status", "rides", ["driver_id", "status"])
    op.create_index("idx_rides_status_location", "rides", ["status", "pickup_lat", "pickup_lng"])


def downgrade() -> None:
    op.drop_index("idx_rides_status_location", table_name="rides")
    op.drop_index("idx_rides_driver_status", table_name="rides")
    op.drop_index("idx_rides_driver_requested", table_name="rides")
    op.drop_index("idx_rides_rider_requested", table_name="rides")
