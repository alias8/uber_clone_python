"""Ride/driver/rating domain logic. Ported from RideService.kt, DriverService.kt and
RatingService.kt. Fare calculation is now in-process (ride_service.pricing) rather than a gRPC
call — see pricing.py's docstring. Kafka event publishing (ride-requested/accepted/completed/
cancelled) and the SSE location/offer streams are deferred to M4/M5.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status

from ride_service.dispatch import NearbyDriver, find_nearby_available_drivers
from ride_service.models import Driver, Rating, Ride, RideStatus
from ride_service.pricing import SURGE_SEARCH_RADIUS_KM, PricingService
from ride_service.repositories import (
    ACTIVE_RIDE_STATUSES,
    DriverRepository,
    RatingRepository,
    RideRepository,
    UserRepository,
)


class DriverService:
    def __init__(self, driver_repository: DriverRepository) -> None:
        self._driver_repository = driver_repository

    async def register_driver(self, user_id: str, vehicle_type: str, license_plate: str) -> Driver:
        if await self._driver_repository.exists_by_id(user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Driver profile already exists")
        driver = Driver(user_id=user_id, vehicle_type=vehicle_type, license_plate=license_plate)
        return await self._driver_repository.save(driver)

    async def get_profile(self, user_id: str) -> Driver:
        driver = await self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No driver profile found")
        return driver

    async def go_online(self, user_id: str, lat: float, lng: float) -> Driver:
        driver = await self.get_profile(user_id)
        await self._driver_repository.save(replace(driver, lat=lat, lng=lng))
        return await self.mark_available_by_id(user_id)

    async def go_offline(self, user_id: str) -> Driver:
        driver = await self.get_profile(user_id)
        await self._driver_repository.save(replace(driver, lat=None, lng=None))
        # emitterRegistry.complete(userId) in the original closes this driver's SSE offer
        # stream — deferred until M5 adds SSE.
        return await self.mark_unavailable_by_id(user_id)

    async def mark_available_by_id(self, user_id: str) -> Driver:
        driver = await self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No driver profile found")
        return await self._driver_repository.save(replace(driver, is_available=True))

    async def mark_unavailable_by_id(self, user_id: str) -> Driver:
        driver = await self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No driver profile found")
        return await self._driver_repository.save(replace(driver, is_available=False))

    async def update_location(self, user_id: str, lat: float, lng: float) -> None:
        driver = await self.get_profile(user_id)
        await self._driver_repository.save(replace(driver, lat=lat, lng=lng))
        # The original also emits a driver_location SSE event to the rider on the driver's
        # active ride — deferred until M5.

    async def find_nearby(self, lat: float, lng: float, radius_km: float) -> list[NearbyDriver]:
        drivers = await self._driver_repository.all()
        return find_nearby_available_drivers(lat, lng, drivers, radius_km)


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
        # kafkaEventProducer.publishRideRequested(saved.id) — deferred until M4.
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
            # kafkaEventProducer.publishRideAccepted(saved.id) — deferred until M4.
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
        # kafkaEventProducer.publishRideCompleted(saved.id) — deferred until M4.
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
        # kafkaEventProducer.publishRideCancelled(saved.id) — deferred until M4.
        return saved

    # Calls the in-process pricing module rather than a separate gRPC pricing-service — the
    # deliberate simplification this port makes relative to uber_clone (see pricing.py).
    async def _calculate_fare(self, ride: Ride) -> Decimal:
        pending_rides = await self._ride_repository.count_pending_near(ride.pickup_lat, ride.pickup_lng)
        drivers = await self._driver_repository.all()
        available_drivers = len(
            find_nearby_available_drivers(ride.pickup_lat, ride.pickup_lng, drivers, SURGE_SEARCH_RADIUS_KM)
        )
        fare, _surge = self._pricing_service.get_fare_quote(
            ride.pickup_lat,
            ride.pickup_lng,
            ride.dropoff_lat,
            ride.dropoff_lng,
            pending_rides,
            available_drivers,
        )
        return fare


def _incremental_avg(old_avg: float | None, old_count: int, new_score: int) -> float:
    avg = old_avg if old_avg is not None else 0.0
    return (avg * old_count + new_score) / (old_count + 1)


def _round_to_2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class RatingService:
    def __init__(
        self,
        rating_repository: RatingRepository,
        ride_repository: RideRepository,
        user_repository: UserRepository,
        driver_repository: DriverRepository,
    ) -> None:
        self._rating_repository = rating_repository
        self._ride_repository = ride_repository
        self._user_repository = user_repository
        self._driver_repository = driver_repository

    async def rate(self, ride_id: str, from_user_id: str, score: int, comment: str | None) -> None:
        if not (1 <= score <= 5):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Score must be between 1 and 5")

        ride = await self._ride_repository.find_by_id(ride_id)
        if ride is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found")
        if ride.status != RideStatus.COMPLETED:
            raise HTTPException(status.HTTP_409_CONFLICT, "Can only rate a completed ride")

        if from_user_id == ride.rider_id:
            if ride.driver_id is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "Ride has no assigned driver")
            to_user_id = ride.driver_id
        elif from_user_id == ride.driver_id:
            to_user_id = ride.rider_id
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a participant in this ride")

        if await self._rating_repository.exists_by_ride_id_and_from_user_id(ride_id, from_user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Already rated this ride")

        await self._rating_repository.save(
            Rating(
                ride_id=ride_id,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                score=score,
                comment=comment,
            )
        )

        driver = await self._driver_repository.find_by_id(to_user_id)
        if driver is not None:
            new_avg = _round_to_2(_incremental_avg(driver.avg_rating, driver.rating_count, score))
            await self._driver_repository.save(
                replace(driver, avg_rating=new_avg, rating_count=driver.rating_count + 1)
            )

        user = await self._user_repository.find_by_id(to_user_id)
        if user is not None:
            new_avg = _round_to_2(_incremental_avg(user.avg_rating, user.rating_count, score))
            await self._user_repository.save(
                replace(user, avg_rating=new_avg, rating_count=user.rating_count + 1)
            )
