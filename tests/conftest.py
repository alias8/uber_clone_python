from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from ride_service import state
from ride_service.db import engine as db_engine
from ride_service.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _postgres() -> Iterator[None]:
    """Spins up a throwaway Postgres container for the whole test session and points every
    repository at it (db.engine.get_sessionmaker() is looked up fresh per call, so this one
    configure() call is all that's needed — see repositories.py)."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url = (
            f"postgresql+asyncpg://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        db_engine.configure(url, poolclass=NullPool)

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")

        yield


@pytest.fixture(autouse=True)
async def _reset_state() -> None:
    """DB rows are truncated between tests; process-local state (the driver location overlay,
    the pricing cache, both rate limiters) is still cleared directly."""
    async with db_engine.get_sessionmaker()() as session:
        await session.execute(text("TRUNCATE users, drivers, rides, ratings RESTART IDENTITY CASCADE"))
        await session.commit()
    state.driver_repository.clear()
    state.pricing_service.clear_cache()
    state.ride_request_rate_limiter.clear()
    state.auth_attempt_rate_limiter.clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client: TestClient, username: str, password: str = "hunter2") -> TestClient:
    """Registers a new rider and leaves the returned client's cookie jar authenticated as them."""
    response = client.post("/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    return client


def register_driver(
    client: TestClient, username: str, vehicle_type: str = "sedan", license_plate: str = "ABC123"
) -> TestClient:
    register_and_login(client, username)
    response = client.post(
        "/driver/register", json={"vehicle_type": vehicle_type, "license_plate": license_plate}
    )
    assert response.status_code == 201, response.text
    return client
