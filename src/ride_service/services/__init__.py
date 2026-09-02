"""Ride/driver/rating domain logic. Ported from RideService.kt, DriverService.kt and
RatingService.kt — one module per service, mirroring that one-file-per-service layout. Fare
calculation is now in-process (ride_service.pricing) rather than a gRPC call — see pricing.py's
docstring. Kafka event publishing (ride-requested/accepted/completed/cancelled) and the SSE
location/offer streams are deferred to M4/M5.
"""

from ride_service.services.driver import DriverService
from ride_service.services.rating import RatingService
from ride_service.services.ride import RideService

__all__ = ["DriverService", "RatingService", "RideService"]
