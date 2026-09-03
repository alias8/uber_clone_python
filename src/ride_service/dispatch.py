"""Nearby-driver matching and offer fan-out.

find_nearby_available_drivers is backed by Redis exactly as uber_clone's
DriverService.kt::findNearby is: a GEOSEARCH over the driver geo-index, filtered to members of
the driver availability set. Both keys are written by repositories/driver.py's save() whenever
a driver's location/availability changes — this module only reads them.

fanout_to_nearby_drivers ports DispatchService.kt::fanoutToNearbyDrivers, called from
kafka_consumer.py's ride-requested handler: it's kept in this module rather than a separate
one because it's the same domain (Redis-backed driver dispatch) and calls
find_nearby_available_drivers directly, whereas Kotlin only splits the two because
DriverService.kt's findNearby is also reused by the `/driver/nearby` endpoint on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from ride_service.geo import eta_minutes
from ride_service.models import Ride
from ride_service.redis_client import get_client

DRIVER_GEO_KEY = "drivers:locations"
DRIVER_AVAILABLE_SET = "drivers:available"
RIDE_OFFER_CHANNEL_PREFIX = "ride_offers:"
DISPATCHED_KEY_PREFIX = "dispatched:"
DISPATCHED_TTL_SECONDS = 5 * 60

DEFAULT_SEARCH_RADIUS_KM = 5.0
MAX_RESULTS = 20


@dataclass
class NearbyDriver:
    driver_id: str
    distance_km: float


async def find_nearby_available_drivers(
    lat: float, lng: float, radius_km: float = DEFAULT_SEARCH_RADIUS_KM
) -> list[NearbyDriver]:
    client = get_client()
    # withdist=True guarantees each result is a (member, distance) pair, not a bare member name.
    results = cast(
        "list[tuple[str, float]]",
        await client.geosearch(
            DRIVER_GEO_KEY,
            longitude=lng,
            latitude=lat,
            unit="km",
            radius=radius_km,
            sort="ASC",
            count=MAX_RESULTS,
            withdist=True,
        ),
    )
    if not results:
        return []

    driver_ids = [driver_id for driver_id, _distance_km in results]
    available = cast("list[int]", await client.smismember(DRIVER_AVAILABLE_SET, driver_ids))
    return [
        NearbyDriver(driver_id=driver_id, distance_km=distance_km)
        for (driver_id, distance_km), is_available in zip(results, available, strict=True)
        if is_available
    ]


async def get_driver_location(driver_id: str) -> tuple[float, float] | None:
    """Ported from DriverService.kt::getDriverLocation. Returns (lat, lng) — note GEOPOS itself
    returns (lon, lat), Redis's convention; this flips it to match the rest of this codebase."""
    positions = cast("list[tuple[float, float] | None]", await get_client().geopos(DRIVER_GEO_KEY, driver_id))
    position = positions[0]
    if position is None:
        return None
    lng, lat = position
    return lat, lng


async def fanout_to_nearby_drivers(ride: Ride) -> None:
    nearby = await find_nearby_available_drivers(ride.pickup_lat, ride.pickup_lng, DEFAULT_SEARCH_RADIUS_KM)
    if not nearby:
        return

    client = get_client()
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride.id}"
    await client.sadd(dispatched_key, *(driver.driver_id for driver in nearby))
    await client.expire(dispatched_key, DISPATCHED_TTL_SECONDS)

    for driver in nearby:
        # camelCase keys: this is a wire format for whatever eventually subscribes to it
        # (M5's SSE layer), not internal Python state — matching Kotlin's JSON payload exactly.
        payload = json.dumps(
            {
                "rideId": ride.id,
                "pickupLat": ride.pickup_lat,
                "pickupLng": ride.pickup_lng,
                "dropoffLat": ride.dropoff_lat,
                "dropoffLng": ride.dropoff_lng,
                "estimatedFare": float(ride.estimated_fare) if ride.estimated_fare is not None else None,
                "etaMinutes": eta_minutes(driver.distance_km),
            }
        )
        await client.publish(f"{RIDE_OFFER_CHANNEL_PREFIX}{driver.driver_id}", payload)
