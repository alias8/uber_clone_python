"""Ported from KafkaConsumer.kt's four @KafkaListener methods."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import cast

from aiokafka import AIOKafkaConsumer

from ride_service import sse
from ride_service.config import settings
from ride_service.dispatch import DISPATCHED_KEY_PREFIX, fanout_to_nearby_drivers, get_driver_location
from ride_service.geo import eta_minutes, haversine_km
from ride_service.kafka_producer import (
    RIDE_ACCEPTED_TOPIC,
    RIDE_CANCELLED_TOPIC,
    RIDE_COMPLETED_TOPIC,
    RIDE_REQUESTED_TOPIC,
)
from ride_service.models import RideStatus
from ride_service.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CONSUMER_GROUP_ID = "feed-fanout-group"

_bootstrap_servers: str | None = None
_consumer: AIOKafkaConsumer | None = None
_task: asyncio.Task[None] | None = None


def configure(bootstrap_servers: str) -> None:
    global _bootstrap_servers
    _bootstrap_servers = bootstrap_servers


async def start() -> None:
    global _consumer, _task
    _consumer = AIOKafkaConsumer(
        RIDE_REQUESTED_TOPIC,
        RIDE_ACCEPTED_TOPIC,
        RIDE_COMPLETED_TOPIC,
        RIDE_CANCELLED_TOPIC,
        bootstrap_servers=_bootstrap_servers or settings.kafka_bootstrap_servers,
        group_id=CONSUMER_GROUP_ID,
        auto_offset_reset="earliest",
    )
    await _consumer.start()
    _task = asyncio.create_task(_consume_loop(_consumer))


async def stop() -> None:
    global _consumer, _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _consumer is not None:
        await _consumer.stop()
        _consumer = None


async def _consume_loop(consumer: AIOKafkaConsumer) -> None:
    async for message in consumer:
        ride_id = message.value.decode("utf-8")
        try:
            await _HANDLERS[message.topic](ride_id)
        except Exception:
            logger.exception("Failed handling %s for ride %s", message.topic, ride_id)


async def handle_ride_requested(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None or ride.status != RideStatus.REQUESTED:
        return
    logger.info("Ride requested: id=%s rider=%s status=%s", ride.id, ride.rider_id, ride.status)
    await fanout_to_nearby_drivers(ride)


async def handle_ride_accepted(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None:
        return
    logger.info("Ride accepted: id=%s driver=%s rider=%s", ride.id, ride.driver_id, ride.rider_id)

    if ride.driver_id is not None:
        position = await get_driver_location(ride.driver_id)
        if position is not None:
            lat, lng = position
            eta = eta_minutes(haversine_km(lat, lng, ride.pickup_lat, ride.pickup_lng))
            sse.emit(ride.id, "driver_eta_to_pickup", json.dumps({"etaMinutes": eta}))

    redis_client = get_redis_client()
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
    dispatched_drivers = cast("set[str]", await redis_client.smembers(dispatched_key))
    payload = json.dumps({"rideId": ride_id})
    for driver_id in dispatched_drivers:
        if driver_id != ride.driver_id:
            sse.emit(driver_id, "offer_cancelled", payload)
    await redis_client.delete(dispatched_key)


async def handle_ride_completed(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None or ride.status != RideStatus.COMPLETED:
        return
    logger.info(
        "Ride completed: id=%s fare=%s driver=%s rider=%s", ride.id, ride.fare, ride.driver_id, ride.rider_id
    )
    sse.complete(ride_id)
    # Payment processing was never implemented in the Kotlin original either (just a TODO
    # comment there).


async def handle_ride_cancelled(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None:
        return
    logger.info("Ride cancelled: id=%s rider=%s", ride.id, ride.rider_id)
    sse.complete(ride_id)


_HANDLERS = {
    RIDE_REQUESTED_TOPIC: handle_ride_requested,
    RIDE_ACCEPTED_TOPIC: handle_ride_accepted,
    RIDE_COMPLETED_TOPIC: handle_ride_completed,
    RIDE_CANCELLED_TOPIC: handle_ride_cancelled,
}
