"""Postgres-backed persistence via SQLAlchemy 2.0 async sessions (M2). Method names
(save/find_by_id/exists_by_*/find_by_*_ordered/clear) are unchanged from M1's in-memory
version — that was the point of choosing them there. Each method opens its own session via
db.engine.get_sessionmaker(), fetched fresh per call rather than captured on the instance, so
tests can repoint every repository at a testcontainers Postgres with one configure() call.

Driver lat/lng are the one exception: they're not columns in uber_clone's schema (that's a
Redis GEOSEARCH index in the real system, wired up here in M3) so they stay in an in-memory
overlay dict here, merged onto rows read from Postgres — see models.py/dispatch.py for the same
note from M1.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import DriverRow, RatingRow, RideRow, UserRow
from ride_service.models import Driver, Rating, Ride, RideStatus, Role, User

ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.MATCHED, RideStatus.IN_PROGRESS)


def _user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        role=Role(row.role),
        avg_rating=row.avg_rating,
        rating_count=row.rating_count,
    )


def _user_to_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        username=user.username,
        password_hash=user.password_hash,
        role=user.role.value,
        avg_rating=user.avg_rating,
        rating_count=user.rating_count,
    )


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


def _rating_from_row(row: RatingRow) -> Rating:
    return Rating(
        id=row.id,
        ride_id=row.ride_id,
        from_user_id=row.from_user_id,
        to_user_id=row.to_user_id,
        score=row.score,
        comment=row.comment,
        created_at=row.created_at,
    )


def _rating_to_row(rating: Rating) -> RatingRow:
    return RatingRow(
        id=rating.id,
        ride_id=rating.ride_id,
        from_user_id=rating.from_user_id,
        to_user_id=rating.to_user_id,
        score=rating.score,
        comment=rating.comment,
        created_at=rating.created_at,
    )


class UserRepository:
    async def save(self, user: User) -> User:
        async with get_sessionmaker()() as session:
            merged = await session.merge(_user_to_row(user))
            await session.commit()
            return _user_from_row(merged)

    async def find_by_id(self, user_id: str) -> User | None:
        async with get_sessionmaker()() as session:
            row = await session.get(UserRow, user_id)
            return _user_from_row(row) if row is not None else None

    async def find_by_username(self, username: str) -> User | None:
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
            return _user_from_row(row) if row is not None else None

    async def exists_by_username(self, username: str) -> bool:
        async with get_sessionmaker()() as session:
            return bool(
                (
                    await session.execute(select(exists().where(UserRow.username == username)))
                ).scalar_one()
            )


class DriverRepository:
    def __init__(self) -> None:
        # In-memory stand-in for the Redis geo-index, until M3 — see module docstring.
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

    async def count_pending_near(self, lat: float, lng: float, delta: float = 0.01) -> int:
        async with get_sessionmaker()() as session:
            stmt = select(func.count()).where(
                RideRow.status == RideStatus.REQUESTED.value,
                RideRow.pickup_lat.between(lat - delta, lat + delta),
                RideRow.pickup_lng.between(lng - delta, lng + delta),
            )
            return int((await session.execute(stmt)).scalar_one())


class RatingRepository:
    async def save(self, rating: Rating) -> Rating:
        async with get_sessionmaker()() as session:
            merged = await session.merge(_rating_to_row(rating))
            await session.commit()
            return _rating_from_row(merged)

    async def exists_by_ride_id_and_from_user_id(self, ride_id: str, from_user_id: str) -> bool:
        async with get_sessionmaker()() as session:
            stmt = exists().where(RatingRow.ride_id == ride_id, RatingRow.from_user_id == from_user_id)
            return bool((await session.execute(select(stmt))).scalar_one())
