"""Ported from RideService.kt. Fare calculation is now in-process (ride_service.pricing) rather
than a gRPC call — see pricing.py's docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status

from ride_service import kafka_producer
from ride_service.dispatch import find_nearby_available_drivers
from ride_service.models import Ride, RideStatus
from ride_service.pricing import SURGE_SEARCH_RADIUS_KM, PricingService
from ride_service.repositories import ACTIVE_RIDE_STATUSES, DriverRepository, RideRepository
from ride_service.services.driver import DriverService


class RideService:
    def __init__(
        self,
        ride_repository: RideRepository,
        driver_repository: DriverRepository,
        driver_service: DriverService,
        pricing_service: PricingService,
    ) -> None:
        self._ride_repository = ride_repository
        self._driver_repository = driver_repository
        self._driver_service = driver_service
        self._pricing_service = pricing_service
        # Guards the accept-ride check-then-write sequence. uber_clone relies on JPA optimistic
        # locking (a `version` column + ObjectOptimisticLockingFailureException) to make two
        # concurrent accepts of the same ride safe; the app is a single asyncio event loop, so
        # an asyncio.Lock around the same sequence gives the same guarantee.
        self._accept_lock = asyncio.Lock()

    async def request_ride(
        self, rider_id: str, pickup_lat: float, pickup_lng: float, dropoff_lat: float, dropoff_lng: float
    ) -> Ride:
        if await self._ride_repository.exists_by_rider_id_and_status_in(rider_id, ACTIVE_RIDE_STATUSES):
            raise HTTPException(status.HTTP_409_CONFLICT, "Rider already has an active ride")

        ride = Ride(
            rider_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            dropoff_lat=dropoff_lat,
            dropoff_lng=dropoff_lng,
        )
        estimated_fare = await self._calculate_fare(ride)
        saved = await self._ride_repository.save(replace(ride, estimated_fare=estimated_fare))
        await kafka_producer.publish_ride_requested(saved.id)
        return saved

    async def get_ride(self, ride_id: str) -> Ride:
        ride = await self._ride_repository.find_by_id(ride_id)
        if ride is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found")
        return ride

    async def accept_ride(self, ride_id: str, driver_id: str) -> Ride:
        async with self._accept_lock:
            ride = await self.get_ride(ride_id)
            if ride.status != RideStatus.REQUESTED:
                raise HTTPException(status.HTTP_409_CONFLICT, "Ride is not available for acceptance")
            driver = await self._driver_repository.find_by_id(driver_id)
            if driver is None:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "No driver profile found — register as a driver first"
                )
            if not driver.is_available:
                raise HTTPException(status.HTTP_409_CONFLICT, "Driver is not currently available")

            await self._driver_service.mark_unavailable_by_id(driver_id)
            saved = await self._ride_repository.save(
                replace(ride, driver_id=driver_id, status=RideStatus.MATCHED, version=ride.version + 1)
            )
            await kafka_producer.publish_ride_accepted(saved.id)
            return saved

    async def start_ride(self, ride_id: str, driver_id: str) -> Ride:
        ride = await self.get_ride(ride_id)
        if ride.status != RideStatus.MATCHED:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ride is not in MATCHED state")
        if ride.driver_id != driver_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not the assigned driver for this ride")
        return await self._ride_repository.save(
            replace(ride, status=RideStatus.IN_PROGRESS, version=ride.version + 1)
        )

    async def complete_ride(self, ride_id: str, driver_id: str) -> Ride:
        ride = await self.get_ride(ride_id)
        if ride.status != RideStatus.IN_PROGRESS:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ride is not IN_PROGRESS")
        if ride.driver_id != driver_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not the assigned driver for this ride")

        saved = await self._ride_repository.save(
            replace(
                ride,
                status=RideStatus.COMPLETED,
                fare=ride.estimated_fare,
                completed_at=datetime.now(UTC),
                version=ride.version + 1,
            )
        )
        await self._driver_service.mark_available_by_id(driver_id)
        await kafka_producer.publish_ride_completed(saved.id)
        return saved

    async def cancel_ride(self, ride_id: str, user_id: str) -> Ride:
        ride = await self.get_ride(ride_id)
        if ride.status in (RideStatus.IN_PROGRESS, RideStatus.COMPLETED):
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Cannot cancel a ride with status {ride.status.value}"
            )
        if ride.rider_id != user_id and ride.driver_id != user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a participant in this ride")

        if ride.driver_id is not None:
            await self._driver_service.mark_available_by_id(ride.driver_id)

        saved = await self._ride_repository.save(
            replace(ride, status=RideStatus.CANCELLED, version=ride.version + 1)
        )
        await kafka_producer.publish_ride_cancelled(saved.id)
        return saved

    # Calls the in-process pricing module rather than a separate gRPC pricing-service — the
    # deliberate simplification this port makes relative to uber_clone (see pricing.py).
    async def _calculate_fare(self, ride: Ride) -> Decimal:
        pending_rides = await self._ride_repository.count_pending_near(ride.pickup_lat, ride.pickup_lng)
        nearby = await find_nearby_available_drivers(
            ride.pickup_lat, ride.pickup_lng, SURGE_SEARCH_RADIUS_KM
        )
        available_drivers = len(nearby)
        fare, _surge = await self._pricing_service.get_fare_quote(
            ride.pickup_lat,
            ride.pickup_lng,
            ride.dropoff_lat,
            ride.dropoff_lng,
            pending_rides,
            available_drivers,
        )
        return fare
