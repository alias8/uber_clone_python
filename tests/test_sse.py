"""Ported from EmitterRegistry.kt/RideOfferListener.kt, plus the SSE call sites in
DriverService.kt and KafkaConsumer.kt.

TestClient's httpx-based transport buffers a response's full body before returning control
(confirmed with a throwaway spike), which makes it unusable for reading a genuinely open-ended
SSE stream in a test — it just hangs. So these tests exercise the registry and the various
emit()/complete() call sites directly (ride_service.sse's queue), and the router guard checks
that reject *before* a StreamingResponse is ever created (which return an ordinary, complete
response — no hang) — not a live end-to-end stream read. The actual streaming behavior is
covered by the manual smoke test (curl -N against the real running app)."""

import asyncio
import json
from dataclasses import replace

from fastapi.testclient import TestClient

from ride_service import kafka_consumer, sse
from ride_service.dispatch import DISPATCHED_KEY_PREFIX, RIDE_OFFER_CHANNEL_PREFIX
from ride_service.main import app
from ride_service.models import RideStatus
from ride_service.redis_client import get_client
from ride_service.state import driver_service, ride_repository
from tests.conftest import register_and_login, register_driver

RIDE_REQUEST = {
    "pickup_lat": 40.7128,
    "pickup_lng": -74.0060,
    "dropoff_lat": 40.7300,
    "dropoff_lng": -74.0000,
}


def _new_client() -> TestClient:
    return TestClient(app)


async def test_registry_emit_and_complete() -> None:
    queue = sse.register("key1")
    sse.emit("key1", "foo", '{"a":1}')
    assert await queue.get() == ("foo", '{"a":1}')

    sse.emit("no-such-key", "foo", "{}")  # no-op, must not raise

    sse.complete("key1")
    assert await queue.get() is None


def test_registry_format_event() -> None:
    assert sse.format_event("foo", '{"a":1}') == 'event: foo\ndata: {"a":1}\n\n'


async def test_go_offline_completes_the_drivers_own_offer_stream(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    alice_id = client.get("/auth/me").json()["user_id"]

    queue = sse.register(alice_id)
    client.post("/driver/mode/off")
    assert await asyncio.wait_for(queue.get(), timeout=2) is None


async def test_update_location_emits_driver_location_for_an_active_ride(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver.post(f"/rides/{ride_id}/accept")
    driver_id = driver.get("/auth/me").json()["user_id"]

    queue = sse.register(ride_id)
    await driver_service.update_location(driver_id, 40.72, -74.01)

    item = await asyncio.wait_for(queue.get(), timeout=2)
    assert item is not None
    event, payload = item
    assert event == "driver_location"
    data = json.loads(payload)
    assert data["lat"] == 40.72
    assert data["lng"] == -74.01
    assert data["etaMinutes"] >= 1


async def test_update_location_emits_nothing_without_an_active_ride(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    alice_id = client.get("/auth/me").json()["user_id"]

    queue = sse.register(alice_id)
    await driver_service.update_location(alice_id, 40.72, -74.01)

    assert queue.empty()


async def test_handle_ride_accepted_notifies_eta_and_cancels_other_offers(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    winner = _new_client()
    register_driver(winner, "winner")
    winner.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    winner_id = winner.get("/auth/me").json()["user_id"]

    loser = _new_client()
    register_driver(loser, "loser")
    loser.post("/driver/mode/on", json={"lat": 40.713, "lng": -74.006})
    loser_id = loser.get("/auth/me").json()["user_id"]

    await kafka_consumer.handle_ride_requested(ride_id)
    assert await get_client().smembers(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == {winner_id, loser_id}

    winner.post(f"/rides/{ride_id}/accept")

    ride_queue = sse.register(ride_id)
    loser_queue = sse.register(loser_id)

    await kafka_consumer.handle_ride_accepted(ride_id)

    eta_item = await asyncio.wait_for(ride_queue.get(), timeout=2)
    assert eta_item is not None
    eta_event, eta_payload = eta_item
    assert eta_event == "driver_eta_to_pickup"
    assert json.loads(eta_payload)["etaMinutes"] >= 1

    cancel_item = await asyncio.wait_for(loser_queue.get(), timeout=2)
    assert cancel_item is not None
    cancel_event, cancel_payload = cancel_item
    assert cancel_event == "offer_cancelled"
    assert json.loads(cancel_payload) == {"rideId": ride_id}

    assert await get_client().exists(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == 0


async def test_handle_ride_completed_and_cancelled_close_the_ride_stream(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    # handle_ride_completed only completes the stream for a ride that's actually COMPLETED
    # (matches KafkaConsumer.kt's onRideCompleted status guard) — a REQUESTED ride here would
    # never call sse.complete(), leaving queue.get() blocked forever.
    ride = await ride_repository.find_by_id(ride_id)
    assert ride is not None
    await ride_repository.save(replace(ride, status=RideStatus.COMPLETED))

    queue = sse.register(ride_id)
    await kafka_consumer.handle_ride_completed(ride_id)
    assert await asyncio.wait_for(queue.get(), timeout=2) is None

    # COMPLETED isn't an "active" ride status, so the same rider can request a second one.
    other_ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]
    other_queue = sse.register(other_ride_id)
    await kafka_consumer.handle_ride_cancelled(other_ride_id)
    assert await asyncio.wait_for(other_queue.get(), timeout=2) is None


async def test_ride_offer_listener_forwards_pubsub_messages_to_the_registry(client: TestClient) -> None:
    """The listener itself is started by the client fixture's lifespan — this only needs to
    publish and check the registry, not manage the listener's lifecycle."""
    register_driver(client, "alice")
    alice_id = client.get("/auth/me").json()["user_id"]

    queue = sse.register(alice_id)
    payload = json.dumps({"rideId": "some-ride"})
    for _ in range(50):
        await get_client().publish(f"{RIDE_OFFER_CHANNEL_PREFIX}{alice_id}", payload)
        try:
            item = await asyncio.wait_for(queue.get(), timeout=0.2)
        except TimeoutError:
            continue
        assert item is not None
        event, received = item
        assert event == "ride_offer"
        assert received == payload
        break
    else:
        raise AssertionError("ride_offer_listener never forwarded the pub/sub message")


async def test_location_stream_rejects_non_participant_rider(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    stranger = _new_client()
    register_and_login(stranger, "stranger")
    response = stranger.get(f"/rides/{ride_id}/location")
    assert response.status_code == 403


async def test_location_stream_rejects_a_ride_not_yet_matched(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", json=RIDE_REQUEST).json()["id"]

    response = client.get(f"/rides/{ride_id}/location")
    assert response.status_code == 409


async def test_offers_stream_requires_driver_role(client: TestClient) -> None:
    register_and_login(client, "rider1")
    response = client.get("/driver/offers")
    assert response.status_code == 403
