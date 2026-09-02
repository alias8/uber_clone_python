from __future__ import annotations

from sqlalchemy import exists, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import DriverRow
from ride_service.models import Driver


class DriverRepository:
    def __init__(self) -> None:
        # In-memory stand-in for the Redis geo-index, until M3 — see package docstring.
        self._locations: dict[str, tuple[float, float]] = {}

    def _with_location(self, driver: Driver) -> Driver:
        loc = self._locations.get(driver.user_id)
        if loc is None:
            return driver
        driver.lat, driver.lng = loc
        return driver

    async def save(self, driver: Driver) -> Driver:
        if driver.lat is not None and driver.lng is not None:
            self._locations[driver.user_id] = (driver.lat, driver.lng)
        else:
            self._locations.pop(driver.user_id, None)

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
            return self._with_location(self._driver_from_row(merged))

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
            return self._with_location(self._driver_from_row(row)) if row is not None else None

    async def exists_by_id(self, user_id: str) -> bool:
        async with get_sessionmaker()() as session:
            return bool(
                (await session.execute(select(exists().where(DriverRow.user_id == user_id)))).scalar_one()
            )

    async def all(self) -> list[Driver]:
        async with get_sessionmaker()() as session:
            rows = (await session.execute(select(DriverRow))).scalars().all()
            return [self._with_location(self._driver_from_row(r)) for r in rows]

    def clear(self) -> None:
        self._locations.clear()
