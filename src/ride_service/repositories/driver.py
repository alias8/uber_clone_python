from __future__ import annotations

from sqlalchemy import exists, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import DriverRow
from ride_service.dispatch import DRIVER_AVAILABLE_SET, DRIVER_GEO_KEY
from ride_service.models import Driver
from ride_service.redis_client import get_client


class DriverRepository:
    async def save(self, driver: Driver) -> Driver:
        row = DriverRow(
            user_id=driver.user_id,
            vehicle_type=driver.vehicle_type,
            license_plate=driver.license_plate,
            is_available=driver.is_available,
            avg_rating=driver.avg_rating,
            rating_count=driver.rating_count,
        )
        async with get_sessionmaker()() as session:
            merged = await session.merge(row)
            await session.commit()
            saved = self._driver_from_row(merged)

        # Same dual-write as uber_clone's DriverService.kt: a Postgres save plus a separate
        # Redis availability-set call, not one atomic operation. Location is a wholly separate
        # concern (set_location/clear_location below) — Postgres has no lat/lng column at all,
        # so save() must not touch the geo-index, or a plain is_available toggle (which always
        # re-fetches the driver first, with no location info) would wipe it.
        client = get_client()
        if driver.is_available:
            await client.sadd(DRIVER_AVAILABLE_SET, driver.user_id)
        else:
            await client.srem(DRIVER_AVAILABLE_SET, driver.user_id)

        return saved

    async def set_location(self, user_id: str, lat: float, lng: float) -> None:
        await get_client().geoadd(DRIVER_GEO_KEY, [lng, lat, user_id])

    async def clear_location(self, user_id: str) -> None:
        await get_client().zrem(DRIVER_GEO_KEY, user_id)

    @staticmethod
    def _driver_from_row(row: DriverRow) -> Driver:
        return Driver(
            user_id=row.user_id,
            vehicle_type=row.vehicle_type,
            license_plate=row.license_plate,
            is_available=row.is_available,
            avg_rating=row.avg_rating,
            rating_count=row.rating_count,
        )

    async def find_by_id(self, user_id: str) -> Driver | None:
        async with get_sessionmaker()() as session:
            row = await session.get(DriverRow, user_id)
            return self._driver_from_row(row) if row is not None else None

    async def exists_by_id(self, user_id: str) -> bool:
        async with get_sessionmaker()() as session:
            return bool(
                (await session.execute(select(exists().where(DriverRow.user_id == user_id)))).scalar_one()
            )
