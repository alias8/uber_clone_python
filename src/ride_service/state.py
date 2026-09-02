"""Process-wide singletons wiring the app together.

Stands in for Spring's singleton-bean wiring. The four repositories are now backed by Postgres
(see repositories.py, db/engine.py) rather than in-memory dicts; rate limiting and the pricing
cache remain in-process until M3 wires in Redis — that's the next seam.
"""

from ride_service.config import settings
from ride_service.db import engine as db_engine
from ride_service.pricing import PricingService
from ride_service.rate_limit import RateLimiter
from ride_service.repositories import DriverRepository, RatingRepository, RideRepository, UserRepository
from ride_service.services import DriverService, RatingService, RideService

db_engine.configure(settings.database_url)

user_repository = UserRepository()
driver_repository = DriverRepository()
ride_repository = RideRepository()
rating_repository = RatingRepository()
pricing_service = PricingService()

driver_service = DriverService(driver_repository)
ride_service = RideService(ride_repository, driver_repository, driver_service, pricing_service)
rating_service = RatingService(rating_repository, ride_repository, user_repository, driver_repository)

ride_request_rate_limiter = RateLimiter(
    capacity=settings.ride_request_limit_per_minute, window_seconds=60
)
auth_attempt_rate_limiter = RateLimiter(
    capacity=settings.auth_attempts_limit_per_15_min, window_seconds=15 * 60
)
