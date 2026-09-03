"""Ported from DriverService.kt."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi import HTTPException, status

from ride_service import sse
from ride_service.dispatch import NearbyDriver, find_nearby_available_drivers
from ride_service.geo import eta_minutes, haversine_km
from ride_service.models import Driver, RideStatus
from ride_service.repositories import DriverRepository, RideRepository

_ACTIVE_RIDE_STATUSES = (RideStatus.MATCHED, RideStatus.IN_PROGRESS)


class DriverService:
    def __init__(self, driver_repository: DriverRepository, ride_repository: RideRepository) -> None:
        self._driver_repository = driver_repository
        self._ride_repository = ride_repository

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
        await self.get_profile(user_id)
        await self._driver_repository.set_location(user_id, lat, lng)
        return await self.mark_available_by_id(user_id)

    async def go_offline(self, user_id: str) -> Driver:
        await self.get_profile(user_id)
        await self._driver_repository.clear_location(user_id)
        saved = await self.mark_unavailable_by_id(user_id)
        sse.complete(user_id)
        return saved

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
        await self.get_profile(user_id)
        await self._driver_repository.set_location(user_id, lat, lng)
        ride = await self._ride_repository.find_first_by_driver_id_and_status_in(
            user_id, _ACTIVE_RIDE_STATUSES
        )
        if ride is not None:
            eta = eta_minutes(haversine_km(lat, lng, ride.dropoff_lat, ride.dropoff_lng))
            sse.emit(ride.id, "driver_location", json.dumps({"lat": lat, "lng": lng, "etaMinutes": eta}))

    async def find_nearby(self, lat: float, lng: float, radius_km: float) -> list[NearbyDriver]:
        return await find_nearby_available_drivers(lat, lng, radius_km)
