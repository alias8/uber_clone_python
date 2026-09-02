"""Fare + surge quoting.

In uber_clone this logic lives in a standalone gRPC pricing-service. This port keeps it as a
plain in-process module instead — a deliberate scope simplification (see README), not a missing
piece. The formulas and constants are ported exactly from PricingGrpcService.kt so a known
pickup/dropoff pair and a known pending-rides/available-drivers ratio produce the same numbers.

The surge cache is real Redis (SET ... EX), same key format and 30s TTL as
PricingGrpcService.kt's surgeKey()/SURGE_TTL_SECONDS.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from ride_service.geo import haversine_km
from ride_service.redis_client import get_client

BASE_FARE = Decimal("2.00")
PER_KM_RATE = Decimal("1.50")
SURGE_MIN_MULTIPLIER = Decimal("1.0")
SURGE_MAX_MULTIPLIER = Decimal("3.0")
SURGE_SEARCH_RADIUS_KM = 3.0
SURGE_TTL_SECONDS = 30
# Grid cell key: round to 2 decimal places ~= 1km resolution.
SURGE_GRID_PRECISION = 2

TWO_PLACES = Decimal("0.01")


def calculate_base_fare(distance_km: float) -> Decimal:
    return BASE_FARE + PER_KM_RATE * Decimal(str(distance_km))


def compute_surge_multiplier(pending_rides: int, available_drivers: int) -> Decimal:
    if available_drivers == 0:
        return SURGE_MAX_MULTIPLIER
    raw = Decimal(pending_rides) / Decimal(available_drivers)
    clamped = max(SURGE_MIN_MULTIPLIER, min(SURGE_MAX_MULTIPLIER, raw))
    return clamped.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def surge_grid_key(lat: float, lng: float) -> str:
    return f"surge:{lat:.{SURGE_GRID_PRECISION}f}:{lng:.{SURGE_GRID_PRECISION}f}"


def calculate_fare(distance_km: float, surge_multiplier: Decimal) -> Decimal:
    base = calculate_base_fare(distance_km)
    return (base * surge_multiplier).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class SurgeCache:
    async def get(self, key: str) -> Decimal | None:
        cached = cast("str | None", await get_client().get(key))
        return Decimal(cached) if cached is not None else None

    async def set(self, key: str, value: Decimal, ttl_seconds: int = SURGE_TTL_SECONDS) -> None:
        await get_client().set(key, str(value), ex=ttl_seconds)


class PricingService:
    """Computes fare quotes. Callers supply the pending-ride and available-driver counts —
    where those counts come from (Postgres/Redis) is not this module's concern."""

    def __init__(self, cache: SurgeCache | None = None) -> None:
        self._cache = cache or SurgeCache()

    async def get_surge_multiplier(
        self, lat: float, lng: float, pending_rides: int, available_drivers: int
    ) -> Decimal:
        key = surge_grid_key(lat, lng)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        multiplier = compute_surge_multiplier(pending_rides, available_drivers)
        await self._cache.set(key, multiplier)
        return multiplier

    async def get_fare_quote(
        self,
        pickup_lat: float,
        pickup_lng: float,
        dropoff_lat: float,
        dropoff_lng: float,
        pending_rides: int,
        available_drivers: int,
    ) -> tuple[Decimal, Decimal]:
        distance_km = haversine_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
        surge = await self.get_surge_multiplier(pickup_lat, pickup_lng, pending_rides, available_drivers)
        fare = calculate_fare(distance_km, surge)
        return fare, surge
