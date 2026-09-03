"""Ported from EmitterRegistry.kt: a single in-process registry mapping a key (a ride id or a
driver id — different UUID spaces, never colliding) to a live SSE connection's event queue.

This is a genuine single-instance limitation of the Kotlin original too — an SSE connection
only works against whichever instance holds it — not a design this port improves on; see
README's M5 section for why that's a deliberate, not accidental, choice.

`asyncio.Queue` stands in for `SseEmitter`: register() hands the caller a queue to read from,
emit() pushes onto it (a no-op if nothing's registered under that key, same as
`emitters[key]?.send(...)` in Kotlin), and complete() pushes the close sentinel (`None`) and
removes the entry, mirroring `emitters.remove(key)?.complete()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

Event = tuple[str, str]  # (event name, JSON payload)

_emitters: dict[str, asyncio.Queue[Event | None]] = {}


def register(key: str) -> asyncio.Queue[Event | None]:
    queue: asyncio.Queue[Event | None] = asyncio.Queue()
    _emitters[key] = queue
    return queue


def unregister(key: str) -> None:
    """Called when a stream's connection closes — the async equivalent of SseEmitter's
    onCompletion/onTimeout/onError callbacks removing the entry."""
    _emitters.pop(key, None)


def emit(key: str, event: str, payload: str) -> None:
    queue = _emitters.get(key)
    if queue is not None:
        queue.put_nowait((event, payload))


def complete(key: str) -> None:
    queue = _emitters.pop(key, None)
    if queue is not None:
        queue.put_nowait(None)


def format_event(event: str, payload: str) -> str:
    return f"event: {event}\ndata: {payload}\n\n"


async def stream(key: str) -> AsyncIterator[str]:
    """The generator a router hands to StreamingResponse — registers on entry, formats each
    emitted event as SSE, and unregisters on exit (client disconnect or complete())."""
    queue = register(key)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            event, payload = item
            yield format_event(event, payload)
    finally:
        unregister(key)
