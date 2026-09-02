from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, func, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import RideRow
from ride_service.models import Ride, RideStatus

ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.MATCHED, RideStatus.IN_PROGRESS)


def _ride_from_row(row: RideRow) -> Ride:
    return Ride(
        id=row.id,
        rider_id=row.rider_id,
        driver_id=row.driver_id,
        pickup_lat=row.pickup_lat,
        pickup_lng=row.pickup_lng,
        dropoff_lat=row.dropoff_lat,
        dropoff_lng=row.dropoff_lng,
        status=RideStatus(row.status),
        estimated_fare=row.estimated_fare,
        fare=row.fare,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        version=row.version,
    )


def _ride_to_row(ride: Ride) -> RideRow:
    return RideRow(
        id=ride.id,
        rider_id=ride.rider_id,
        driver_id=ride.driver_id,
        pickup_lat=ride.pickup_lat,
        pickup_lng=ride.pickup_lng,
        dropoff_lat=ride.dropoff_lat,
        dropoff_lng=ride.dropoff_lng,
        status=ride.status.value,
        estimated_fare=ride.estimated_fare,
        fare=ride.fare,
        requested_at=ride.requested_at,
        completed_at=ride.completed_at,
        version=ride.version,
    )


class RideRepository:
    async def save(self, ride: Ride) -> Ride:
        async with get_sessionmaker()() as session:
            merged = await session.merge(_ride_to_row(ride))
            await session.commit()
            return _ride_from_row(merged)

    async def find_by_id(self, ride_id: str) -> Ride | None:
        async with get_sessionmaker()() as session:
            row = await session.get(RideRow, ride_id)
            return _ride_from_row(row) if row is not None else None

    async def exists_by_rider_id_and_status_in(
        self, rider_id: str, statuses: tuple[RideStatus, ...]
    ) -> bool:
        async with get_sessionmaker()() as session:
            status_values = [s.value for s in statuses]
            stmt = exists().where(RideRow.rider_id == rider_id, RideRow.status.in_(status_values))
            return bool((await session.execute(select(stmt))).scalar_one())

    async def find_by_rider_id_ordered(self, rider_id: str) -> list[Ride]:
        async with get_sessionmaker()() as session:
            stmt = (
                select(RideRow).where(RideRow.rider_id == rider_id).order_by(RideRow.requested_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_ride_from_row(r) for r in rows]

    async def find_by_driver_id_ordered(self, driver_id: str) -> list[Ride]:
        async with get_sessionmaker()() as session:
            stmt = (
                select(RideRow)
                .where(RideRow.driver_id == driver_id)
                .order_by(RideRow.requested_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_ride_from_row(r) for r in rows]

    async def find_by_status_and_requested_at_before(
        self, status: RideStatus, cutoff: datetime
    ) -> list[Ride]:
        async with get_sessionmaker()() as session:
            stmt = select(RideRow).where(RideRow.status == status.value, RideRow.requested_at < cutoff)
            rows = (await session.execute(stmt)).scalars().all()
            return [_ride_from_row(r) for r in rows]

    async def count_pending_near(self, lat: float, lng: float, delta: float = 0.01) -> int:
        async with get_sessionmaker()() as session:
            stmt = select(func.count()).where(
                RideRow.status == RideStatus.REQUESTED.value,
                RideRow.pickup_lat.between(lat - delta, lat + delta),
                RideRow.pickup_lng.between(lng - delta, lng + delta),
            )
            return int((await session.execute(stmt)).scalar_one())
