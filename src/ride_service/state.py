"""Process-wide singletons wiring the app together.

Stands in for Spring's singleton-bean wiring. The four repositories are backed by Postgres
(see repositories/, db/engine.py); driver geo/availability, the surge cache, and rate limiting
are backed by Redis (see redis_client.py, rate_limit.py, dispatch.py, repositories/driver.py).
Ride lifecycle events publish to/get consumed from Kafka (see kafka_producer.py,
kafka_consumer.py, stale_ride_retry.py) — those three only get configure()d here; main.py's
lifespan actually starts/stops them, since (unlike the above) they hold no state worth
configuring before the app is actually running.
"""

from ride_service import kafka_consumer, kafka_producer, rate_limit, redis_client
from ride_service.config import settings
from ride_service.db import engine as db_engine
from ride_service.pricing import PricingService
from ride_service.repositories import DriverRepository, RatingRepository, RideRepository, UserRepository
from ride_service.services import DriverService, RatingService, RideService

db_engine.configure(settings.database_url)
redis_client.configure(settings.redis_url)
rate_limit.configure(settings.redis_url)
kafka_producer.configure(settings.kafka_bootstrap_servers)
kafka_consumer.configure(settings.kafka_bootstrap_servers)

user_repository = UserRepository()
driver_repository = DriverRepository()
ride_repository = RideRepository()
rating_repository = RatingRepository()
pricing_service = PricingService()

driver_service = DriverService(driver_repository, ride_repository)
ride_service = RideService(ride_repository, driver_repository, driver_service, pricing_service)
rating_service = RatingService(rating_repository, ride_repository, user_repository, driver_repository)

ride_request_rate_limiter = rate_limit.RateLimiter(
    capacity=settings.ride_request_limit_per_minute, window_seconds=60
)
auth_attempt_rate_limiter = rate_limit.RateLimiter(
    capacity=settings.auth_attempts_limit_per_15_min, window_seconds=15 * 60
)
