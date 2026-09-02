from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from ride_service.geo import eta_minutes, haversine_km
from ride_service.models import Driver, Ride, RideStatus

# --- auth ---


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class SwitchModeRequest(BaseModel):
    mode: str


class AuthResponse(BaseModel):
    token: str


class MeResponse(BaseModel):
    user_id: str


# --- rides ---


class RideRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float


class RideResponse(BaseModel):
    id: str
    rider_id: str
    driver_id: str | None
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    status: RideStatus
    estimated_fare: Decimal | None
    fare: Decimal | None
    requested_at: datetime
    completed_at: datetime | None
    estimated_journey_minutes: int


# --- driver ---


class DriverRegisterRequest(BaseModel):
    vehicle_type: str
    license_plate: str


class DriverProfileResponse(BaseModel):
    user_id: str
    vehicle_type: str
    license_plate: str
    is_available: bool
    avg_rating: float | None


class DriverLocationRequest(BaseModel):
    lat: float
    lng: float


class NearbyDriverResponse(BaseModel):
    driver_id: str
    distance_km: float


# --- rating ---


class RatingRequest(BaseModel):
    # Range (1-5) is enforced in the service layer, matching RatingService.kt's 400 response —
    # not a Pydantic Field constraint, which would 422 instead.
    score: int
    comment: str | None = None


# --- domain -> response mapping (ported from RideDtos.kt / DriverDtos.kt) ---


def ride_to_response(ride: Ride) -> RideResponse:
    distance_km = haversine_km(ride.pickup_lat, ride.pickup_lng, ride.dropoff_lat, ride.dropoff_lng)
    return RideResponse(
        id=ride.id,
        rider_id=ride.rider_id,
        driver_id=ride.driver_id,
        pickup_lat=ride.pickup_lat,
        pickup_lng=ride.pickup_lng,
        dropoff_lat=ride.dropoff_lat,
        dropoff_lng=ride.dropoff_lng,
        status=ride.status,
        estimated_fare=ride.estimated_fare,
        fare=ride.fare,
        requested_at=ride.requested_at,
        completed_at=ride.completed_at,
        estimated_journey_minutes=eta_minutes(distance_km),
    )


def driver_to_response(driver: Driver) -> DriverProfileResponse:
    return DriverProfileResponse(
        user_id=driver.user_id,
        vehicle_type=driver.vehicle_type,
        license_plate=driver.license_plate,
        is_available=driver.is_available,
        avg_rating=driver.avg_rating,
    )
