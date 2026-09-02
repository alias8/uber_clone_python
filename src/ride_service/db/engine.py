"""The async engine/session-factory seam. Repositories call get_sessionmaker() fresh on every
method rather than capturing one at construction, so tests can point the whole app at a
throwaway testcontainers Postgres with a single configure() call — see tests/conftest.py.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import Pool

from ride_service.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(database_url: str, *, poolclass: type[Pool] | None = None) -> None:
    """(Re)builds the engine and session factory. create_async_engine() doesn't connect
    eagerly, so calling this at import time (state.py) or again later (tests) is both safe.

    tests/conftest.py passes poolclass=NullPool: a TestClient runs the ASGI app in its own
    background-thread event loop per test, distinct from the loop pytest-asyncio hands to the
    test function itself, and asyncpg connections are bound to the loop that opened them —
    pooling would hand a test a connection opened on a different loop and blow up. NullPool
    opens (and closes) a fresh connection per checkout, so nothing persists across loops. The
    real app runs a single uvicorn event loop, so normal pooling is fine there.
    """
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url, poolclass=poolclass)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        configure(settings.database_url)
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose() -> None:
    """Closes pooled connections. Deliberately doesn't clear _engine/_sessionmaker — an
    AsyncEngine stays usable after dispose() (it just opens fresh connections on next
    checkout), which matters because main.py's lifespan calls this on every FastAPI shutdown
    and tests spin up a new TestClient (hence a new lifespan) per test against the one
    session-scoped engine configured in conftest.py."""
    if _engine is not None:
        await _engine.dispose()
