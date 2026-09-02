"""Ported from StaleRideRetryJob.kt: a background loop that re-publishes ride-requested for any
ride still REQUESTED (never accepted) after RETRY_CUTOFF, in case the original dispatch found
no nearby drivers and the situation has since changed. retry_once() is exported separately from
the loop so it's directly callable in tests without waiting out RETRY_INTERVAL_SECONDS.

No connection of its own to configure — it only calls into kafka_producer (Kafka) and
ride_repository (Postgres), both already configured/started elsewhere (state.py/main.py).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from ride_service import kafka_producer
from ride_service.models import RideStatus

logger = logging.getLogger(__name__)

RETRY_INTERVAL_SECONDS = 60
RETRY_CUTOFF = timedelta(minutes=2)

_task: asyncio.Task[None] | None = None


async def start() -> None:
    global _task
    _task = asyncio.create_task(_retry_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


async def _retry_loop() -> None:
    while True:
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        await retry_once()


async def retry_once() -> None:
    from ride_service.state import ride_repository

    cutoff = datetime.now(UTC) - RETRY_CUTOFF
    stale = await ride_repository.find_by_status_and_requested_at_before(RideStatus.REQUESTED, cutoff)
    if not stale:
        return

    logger.info("Retrying %d stale ride(s)", len(stale))
    for ride in stale:
        logger.info("Re-dispatching stale ride %s, requested at %s", ride.id, ride.requested_at)
        await kafka_producer.publish_ride_requested(ride.id)
