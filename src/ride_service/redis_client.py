"""The async Redis client seam, mirroring db/engine.py's configure()/get_client()/dispose()
shape. Used for the driver geo-index, the driver availability set, and the surge cache
(rate_limit.py has its own, separate seam — see that module's docstring for why).

get_client() hands back one client per *running event loop* rather than one process-wide
singleton: a TestClient runs the ASGI app in its own background-thread event loop per test,
distinct from the loop pytest-asyncio hands the test function itself, and a redis-py client's
connections are bound to the loop that opened them — sharing one client across loops raises
"attached to a different loop". The real app runs a single uvicorn event loop for its whole
lifetime, so this is exactly one client there, same as a plain singleton would be.
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from ride_service.config import settings

_url: str | None = None
_clients: dict[asyncio.AbstractEventLoop, Redis] = {}


def configure(url: str) -> None:
    global _url, _clients
    _url = url
    _clients = {}


def get_client() -> Redis:
    global _url
    if _url is None:
        _url = settings.redis_url
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = Redis.from_url(_url, decode_responses=True)
        _clients[loop] = client
    return client


async def dispose() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    client = _clients.pop(loop, None)
    if client is not None:
        await client.aclose()
