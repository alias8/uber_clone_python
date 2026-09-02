"""Ported from RatingService.kt."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status

from ride_service.models import Rating, RideStatus
from ride_service.repositories import DriverRepository, RatingRepository, RideRepository, UserRepository


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
