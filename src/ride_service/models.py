from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Role(StrEnum):
    RIDER = "RIDER"
    DRIVER = "DRIVER"


class RideStatus(StrEnum):
    REQUESTED = "REQUESTED"
    MATCHED = "MATCHED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class User:
    username: str
    password_hash: str
    id: str = field(default_factory=_new_id)
    role: Role = Role.RIDER
    avg_rating: float | None = None
    rating_count: int = 0


@dataclass
class Driver:
    # Same id as the User this profile belongs to — one Driver profile per User account.
    user_id: str
    vehicle_type: str
    license_plate: str
    is_available: bool = False
    avg_rating: float | None = None
    rating_count: int = 0
    # In-memory stand-in for the Redis geo-index used from M3 onward.
    lat: float | None = None
    lng: float | None = None


@dataclass
class Ride:
    rider_id: str
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    id: str = field(default_factory=_new_id)
    driver_id: str | None = None
    status: RideStatus = RideStatus.REQUESTED
    estimated_fare: Decimal | None = None
    fare: Decimal | None = None
    requested_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None
    version: int = 0


@dataclass
class Rating:
    ride_id: str
    from_user_id: str
    to_user_id: str
    score: int
    comment: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)
