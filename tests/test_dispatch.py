"""Ported from DispatchService.kt (fan-out) and StaleRideRetryJob.kt (retry), plus the parts of
KafkaConsumer.kt's ride-requested/ride-accepted handlers that have real (non-SSE) behavior."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiokafka import AIOKafkaConsumer
from fastapi.testclient import TestClient

from ride_service import kafka_consumer, kafka_producer
from ride_service.dispatch import (
    DISPATCHED_KEY_PREFIX,
    DISPATCHED_TTL_SECONDS,
    RIDE_OFFER_CHANNEL_PREFIX,
    fanout_to_nearby_drivers,
)
from ride_service.main import app
from ride_service.models import Ride
from ride_service.redis_client import get_redis_client
from ride_service.stale_ride_retry import RETRY_CUTOFF, retry_once
from ride_service.state import ride_repository
from tests.conftest import register_and_login, register_driver

RIDE_REQUEST = {
    "pickup_lat": 40.7128,
    "pickup_lng": -74.0060,
    "dropoff_lat": 40.7300,
    "dropoff_lng": -74.0000,
}


def _new_client() -> TestClient:
    return TestClient(app)


async def test_fanout_writes_dispatched_set_with_ttl_and_publishes_offer(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    alice_id = client.get("/auth/me").json()["user_id"]

    redis = get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"{RIDE_OFFER_CHANNEL_PREFIX}{alice_id}")
    # subscribe() sends SUBSCRIBE without reading the response (redis-py deliberately leaves it
    # for get_message(), so it doesn't risk swallowing a real message) — drain that
    # confirmation now so the next get_message() call waits for the actual published offer.
    confirmation = await pubsub.get_message(timeout=2)
    assert confirmation is not None and confirmation["type"] == "subscribe"

    ride = Ride(
        rider_id="rider-x",
        pickup_lat=RIDE_REQUEST["pickup_lat"],
        pickup_lng=RIDE_REQUEST["pickup_lng"],
        dropoff_lat=RIDE_REQUEST["dropoff_lat"],
        dropoff_lng=RIDE_REQUEST["dropoff_lng"],
        estimated_fare=Decimal("9.99"),
    )
    await fanout_to_nearby_drivers(ride)

    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride.id}"
    assert await redis.smembers(dispatched_key) == {alice_id}
    ttl = await redis.ttl(dispatched_key)
    assert 0 < ttl <= DISPATCHED_TTL_SECONDS

    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert message is not None
    payload = json.loads(message["data"])
    assert payload == {
        "rideId": ride.id,
        "pickupLat": ride.pickup_lat,
        "pickupLng": ride.pickup_lng,
        "dropoffLat": ride.dropoff_lat,
        "dropoffLng": ride.dropoff_lng,
        "estimatedFare": 9.99,
        "etaMinutes": payload["etaMinutes"],
    }
    assert payload["etaMinutes"] >= 1
    await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's stub omits this one's return type


async def test_fanout_with_no_nearby_drivers_writes_nothing(client: TestClient) -> None:
    ride = Ride(
        rider_id="rider-x",
        pickup_lat=RIDE_REQUEST["pickup_lat"],
        pickup_lng=RIDE_REQUEST["pickup_lng"],
        dropoff_lat=RIDE_REQUEST["dropoff_lat"],
        dropoff_lng=RIDE_REQUEST["dropoff_lng"],
    )
    await fanout_to_nearby_drivers(ride)
    assert await get_redis_client().exists(f"{DISPATCHED_KEY_PREFIX}{ride.id}") == 0


async def test_handle_ride_requested_dispatches_when_still_requested(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver_id = driver.get("/auth/me").json()["user_id"]

    await kafka_consumer.handle_ride_requested(ride_id)

    assert await get_redis_client().smembers(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == {driver_id}


async def test_handle_ride_requested_skips_a_ride_that_is_no_longer_requested(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]
    client.post(f"/rides/{ride_id}/cancel")

    await kafka_consumer.handle_ride_requested(ride_id)

    assert await get_redis_client().exists(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == 0


async def test_handle_ride_accepted_clears_the_dispatched_set(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})

    await kafka_consumer.handle_ride_requested(ride_id)
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
    assert await get_redis_client().exists(dispatched_key) == 1

    await kafka_consumer.handle_ride_accepted(ride_id)
    assert await get_redis_client().exists(dispatched_key) == 0


async def test_stale_ride_retry_republishes_an_old_requested_ride(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    ride = await ride_repository.find_by_id(ride_id)
    assert ride is not None
    backdated_at = datetime.now(UTC) - RETRY_CUTOFF - timedelta(seconds=1)
    await ride_repository.save(replace(ride, requested_at=backdated_at))

    # A fresh consumer group so this doesn't race the app's own "feed-fanout-group" consumer
    # (also running, via the client fixture's lifespan) for the same message.
    probe = AIOKafkaConsumer(
        kafka_producer.RIDE_REQUESTED_TOPIC,
        bootstrap_servers=kafka_producer._bootstrap_servers,
        group_id="test-stale-retry-probe",
        auto_offset_reset="latest",
    )
    await probe.start()
    try:
        await retry_once()
        message = await asyncio.wait_for(probe.getone(), timeout=10)
        assert message.value.decode("utf-8") == ride_id
    finally:
        await probe.stop()


async def test_stale_ride_retry_leaves_recent_requested_rides_alone(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    # requested_at defaults to now — well inside RETRY_CUTOFF — so retry_once() must not touch it.
    await retry_once()
    ride = await ride_repository.find_by_id(ride_id)
    assert ride is not None
    assert ride.status.value == "REQUESTED"


async def test_ride_request_is_dispatched_end_to_end_through_kafka(client: TestClient) -> None:
    """The one test exercising the real producer -> topic -> consumer wiring, not just the
    handler functions directly — proving the app's actual lifespan-started consumer works."""
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    alice_id = client.get("/auth/me").json()["user_id"]

    rider = _new_client()
    register_and_login(rider, "rider1")
    ride_id = rider.post("/rides", json=RIDE_REQUEST).json()["id"]

    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
    redis = get_redis_client()
    for _ in range(50):
        if await redis.exists(dispatched_key):
            break
        await asyncio.sleep(0.2)
    else:
        raise AssertionError("consumer never dispatched the ride within 10s")

    assert await redis.smembers(dispatched_key) == {alice_id}
