from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ride_service import kafka_consumer, kafka_producer, redis_client, ride_offer_listener, stale_ride_retry
from ride_service.db import engine as db_engine
from ride_service.routers import auth, driver, rides


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # kafka_producer has no start() — get_producer() lazily creates one per event loop on
    # first publish, same as redis_client.get_redis_client() (see kafka_producer.py's docstring).
    await kafka_consumer.start()
    await stale_ride_retry.start()
    await ride_offer_listener.start()
    yield
    await ride_offer_listener.stop()
    await stale_ride_retry.stop()
    await kafka_consumer.stop()
    await kafka_producer.dispose()
    await db_engine.dispose()
    await redis_client.dispose()


app = FastAPI(title="ride-service", description="Python/FastAPI port of uber_clone.", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(rides.router)
app.include_router(driver.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
