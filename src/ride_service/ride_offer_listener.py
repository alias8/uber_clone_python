"""Ported from RideOfferListener.kt: subscribes to the ride_offers:* pattern (published by
dispatch.py::fanout_to_nearby_drivers) and forwards each message to the driver's own SSE
stream. Started/stopped in main.py's lifespan, same shape as kafka_consumer.py.
"""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio.client import PubSub

from ride_service import sse
from ride_service.dispatch import RIDE_OFFER_CHANNEL_PREFIX
from ride_service.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_pubsub: PubSub | None = None
_task: asyncio.Task[None] | None = None


async def start() -> None:
    global _pubsub, _task
    _pubsub = get_redis_client().pubsub()
    await _pubsub.psubscribe(f"{RIDE_OFFER_CHANNEL_PREFIX}*")
    _task = asyncio.create_task(_listen_loop(_pubsub))


async def stop() -> None:
    global _pubsub, _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _pubsub is not None:
        await _pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's stub omits this one's return type
        _pubsub = None


async def _listen_loop(pubsub: PubSub) -> None:
    async for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        channel = message["channel"]
        driver_id = channel.removeprefix(RIDE_OFFER_CHANNEL_PREFIX)
        try:
            sse.emit(driver_id, "ride_offer", message["data"])
        except Exception:
            logger.exception("Failed to push offer to driver %s", driver_id)
