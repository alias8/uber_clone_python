"""Postgres-backed persistence via SQLAlchemy 2.0 async sessions (M2). Method names
(save/find_by_id/exists_by_*/find_by_*_ordered/clear) are unchanged from M1's in-memory
version — that was the point of choosing them there. Each method opens its own session via
db.engine.get_sessionmaker(), fetched fresh per call rather than captured on the instance, so
tests can repoint every repository at a testcontainers Postgres with one configure() call.

Driver lat/lng are the one exception: they're not columns in uber_clone's schema (that's a
Redis GEOSEARCH index in the real system, wired up here in M3) so they stay in an in-memory
overlay dict here, merged onto rows read from Postgres — see models.py/dispatch.py for the same
note from M1.

One module per repository, mirroring uber_clone's one-repository-interface-per-file layout
(UserRepository.kt, DriverRepository.kt, RideRepository.kt, RatingRepository.kt).
"""

from ride_service.repositories.driver import DriverRepository
from ride_service.repositories.rating import RatingRepository
from ride_service.repositories.ride import ACTIVE_RIDE_STATUSES, RideRepository
from ride_service.repositories.user import UserRepository

__all__ = [
    "ACTIVE_RIDE_STATUSES",
    "DriverRepository",
    "RatingRepository",
    "RideRepository",
    "UserRepository",
]
