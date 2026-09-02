from fastapi import FastAPI

from ride_service.routers import auth, driver, rides

app = FastAPI(title="ride-service", description="Python/FastAPI port of uber_clone.")

app.include_router(auth.router)
app.include_router(rides.router)
app.include_router(driver.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
