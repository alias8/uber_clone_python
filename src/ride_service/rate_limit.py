"""Redis-backed rate limiting via the `limits` package, replacing uber_clone's Bucket4j-based
RateLimiterService.kt. Fixed-window, not a true token bucket — continuing this project's own
choice from M1 rather than switching semantics along with the storage.

`limits`'s async Redis storage doesn't accept an existing client (it manages its own), so this
module gets its own small configure()/get_strategy() seam — same shape as redis_client.py,
and for the same reason: get_strategy() hands back one storage/strategy per *running event
loop*, not a process-wide singleton, because the underlying redis-py connections are bound to
the loop that opened them and a TestClient runs the app in its own per-test loop — see
redis_client.py's docstring for the full explanation.
"""

from __future__ import annotations

import asyncio

from limits import RateLimitItemPerSecond
from limits.aio.strategies import FixedWindowRateLimiter
from limits.storage import storage_from_string

from ride_service.config import settings

_url: str | None = None
_strategies: dict[asyncio.AbstractEventLoop, FixedWindowRateLimiter] = {}


def configure(redis_url: str) -> None:
    global _url, _strategies
    _url = redis_url
    _strategies = {}


def get_strategy() -> FixedWindowRateLimiter:
    global _url
    if _url is None:
        _url = settings.redis_url
    loop = asyncio.get_running_loop()
    strategy = _strategies.get(loop)
    if strategy is None:
        async_url = _url if _url.startswith("async+") else f"async+{_url}"
        storage = storage_from_string(async_url, implementation="redispy")
        strategy = FixedWindowRateLimiter(storage)
        _strategies[loop] = strategy
    return strategy


class RateLimiter:
    def __init__(self, capacity: int, window_seconds: float) -> None:
        self._limit = RateLimitItemPerSecond(capacity, multiples=int(window_seconds))

    async def allow(self, key: str) -> bool:
        return bool(await get_strategy().hit(self._limit, key))
