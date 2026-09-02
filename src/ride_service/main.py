from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ride_service.db import engine as db_engine
from ride_service.routers import auth, driver, rides


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await db_engine.dispose()


app = FastAPI(title="ride-service", description="Python/FastAPI port of uber_clone.", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(rides.router)
app.include_router(driver.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
