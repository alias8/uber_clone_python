"""Ported from KafkaEventProducer.kt. Sends the bare ride id as the message value (no key, no
JSON envelope) on each of the four ride-lifecycle topics — the same wire format
`KafkaTemplate<String, String>.send(topic, rideId)` produces.

Like redis_client.py, get_producer() hands back one producer per *running event loop* rather
than a process-wide singleton: publish_ride_*() is called synchronously from request-handling
code, and a test can juggle several TestClient instances in one test (rider + driver +...) —
only the first one's `with` block actually runs main.py's lifespan, but every instance
processes its own requests on its own portal event loop, so a producer bound to one client's
loop breaks when a sibling client's request tries to use it. See redis_client.py's docstring
for the full explanation.
"""

from __future__ import annotations

import asyncio

from aiokafka import AIOKafkaProducer

from ride_service.config import settings

RIDE_REQUESTED_TOPIC = "ride-requested"
RIDE_ACCEPTED_TOPIC = "ride-accepted"
RIDE_COMPLETED_TOPIC = "ride-completed"
RIDE_CANCELLED_TOPIC = "ride-cancelled"

_bootstrap_servers: str | None = None
_producers: dict[asyncio.AbstractEventLoop, AIOKafkaProducer] = {}


def configure(bootstrap_servers: str) -> None:
    global _bootstrap_servers, _producers
    _bootstrap_servers = bootstrap_servers
    _producers = {}


async def get_producer() -> AIOKafkaProducer:
    global _bootstrap_servers
    if _bootstrap_servers is None:
        _bootstrap_servers = settings.kafka_bootstrap_servers
    loop = asyncio.get_running_loop()
    producer = _producers.get(loop)
    if producer is None:
        producer = AIOKafkaProducer(bootstrap_servers=_bootstrap_servers)
        await producer.start()
        _producers[loop] = producer
    return producer


async def dispose() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    producer = _producers.pop(loop, None)
    if producer is not None:
        await producer.stop()


async def publish_ride_requested(ride_id: str) -> None:
    producer = await get_producer()
    await producer.send_and_wait(RIDE_REQUESTED_TOPIC, ride_id.encode("utf-8"))


async def publish_ride_accepted(ride_id: str) -> None:
    producer = await get_producer()
    await producer.send_and_wait(RIDE_ACCEPTED_TOPIC, ride_id.encode("utf-8"))


async def publish_ride_completed(ride_id: str) -> None:
    producer = await get_producer()
    await producer.send_and_wait(RIDE_COMPLETED_TOPIC, ride_id.encode("utf-8"))


async def publish_ride_cancelled(ride_id: str) -> None:
    producer = await get_producer()
    await producer.send_and_wait(RIDE_CANCELLED_TOPIC, ride_id.encode("utf-8"))
