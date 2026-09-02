"""Ported from DriverService.kt."""

from __future__ import annotations

from dataclasses import replace

from fastapi import HTTPException, status

from ride_service.dispatch import NearbyDriver, find_nearby_available_drivers
from ride_service.models import Driver
from ride_service.repositories import DriverRepository


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
        await self.get_profile(user_id)
        await self._driver_repository.set_location(user_id, lat, lng)
        return await self.mark_available_by_id(user_id)

    async def go_offline(self, user_id: str) -> Driver:
        await self.get_profile(user_id)
        await self._driver_repository.clear_location(user_id)
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
        await self.get_profile(user_id)
        await self._driver_repository.set_location(user_id, lat, lng)
        # The original also emits a driver_location SSE event to the rider on the driver's
        # active ride — deferred until M5.

    async def find_nearby(self, lat: float, lng: float, radius_km: float) -> list[NearbyDriver]:
        return await find_nearby_available_drivers(lat, lng, radius_km)
