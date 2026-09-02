"""Ported from KafkaConsumer.kt's four @KafkaListener methods.

ride-requested is the one handler with real (Redis-observable) M4 behavior: dispatching to
nearby drivers. ride-accepted/-completed/-cancelled in Kotlin exist almost entirely to drive
SSE (EmitterRegistry) — computing a driver's ETA to pickup, notifying other dispatched drivers
their offer was cancelled, closing a ride's location stream — none of which exists in this port
yet (M5). Those lines are commented the same way this project has deferred every other
milestone's out-of-scope pieces (see services/ride.py, services/driver.py). ride-accepted does
keep the one Redis-only piece of cleanup: deleting the now-stale `dispatched:{ride_id}` key.
"""

from __future__ import annotations

import asyncio
import logging

from aiokafka import AIOKafkaConsumer

from ride_service.config import settings
from ride_service.dispatch import DISPATCHED_KEY_PREFIX, fanout_to_nearby_drivers
from ride_service.kafka_producer import (
    RIDE_ACCEPTED_TOPIC,
    RIDE_CANCELLED_TOPIC,
    RIDE_COMPLETED_TOPIC,
    RIDE_REQUESTED_TOPIC,
)
from ride_service.models import RideStatus
from ride_service.redis_client import get_client

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

    # Computing the assigned driver's ETA to pickup and emitting it, plus notifying every other
    # dispatched driver their offer was cancelled, are both SSE (EmitterRegistry) — deferred
    # until M5. The one real cleanup left is dropping the now-stale dispatched-drivers set.
    await get_client().delete(f"{DISPATCHED_KEY_PREFIX}{ride_id}")


async def handle_ride_completed(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None or ride.status != RideStatus.COMPLETED:
        return
    logger.info(
        "Ride completed: id=%s fare=%s driver=%s rider=%s", ride.id, ride.fare, ride.driver_id, ride.rider_id
    )
    # emitterRegistry.complete(rideId) in the original closes the ride's SSE location stream —
    # deferred until M5. Payment processing was never implemented in the Kotlin original either
    # (just a TODO comment there).


async def handle_ride_cancelled(ride_id: str) -> None:
    from ride_service.state import ride_repository

    ride = await ride_repository.find_by_id(ride_id)
    if ride is None:
        return
    logger.info("Ride cancelled: id=%s rider=%s", ride.id, ride.rider_id)
    # emitterRegistry.complete(rideId) — deferred until M5.


_HANDLERS = {
    RIDE_REQUESTED_TOPIC: handle_ride_requested,
    RIDE_ACCEPTED_TOPIC: handle_ride_accepted,
    RIDE_COMPLETED_TOPIC: handle_ride_completed,
    RIDE_CANCELLED_TOPIC: handle_ride_cancelled,
}
