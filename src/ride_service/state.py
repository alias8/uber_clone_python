"""Process-wide singletons for M1's in-memory backing stores.

Stands in for Spring's singleton-bean wiring. From M2 onward these get replaced by proper
FastAPI dependency providers backed by a Postgres session factory / Redis connection pool —
this module is the seam where that swap happens.
"""

from ride_service.config import settings
from ride_service.pricing import PricingService
from ride_service.rate_limit import RateLimiter
from ride_service.repositories import DriverRepository, RatingRepository, RideRepository, UserRepository
from ride_service.services import DriverService, RatingService, RideService

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
