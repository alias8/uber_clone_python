from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from ride_service import kafka_consumer, kafka_producer, rate_limit, redis_client
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


@pytest.fixture(scope="session", autouse=True)
def _redis() -> Iterator[None]:
    """Spins up a throwaway Redis container for the whole test session and points the driver
    geo-index/availability-set client, the surge cache, and rate limiting at it."""
    with RedisContainer("redis:7-alpine") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        redis_client.configure(url)
        rate_limit.configure(url)
        yield


@pytest.fixture(scope="session", autouse=True)
def _kafka() -> Iterator[None]:
    """Spins up a throwaway Kafka broker for the whole test session. The producer/consumer
    themselves are only started per event loop / per app lifespan (see kafka_producer.py's
    per-loop cache and kafka_consumer.py's start()/stop()), so this fixture just needs to point
    both at the broker's address once. The default 30s startup wait is too tight for the
    ZooKeeper-based confluentinc/cp-kafka image this pulls in — 90s is comfortably clear of
    what it's taken locally (~40s)."""
    kafka = KafkaContainer()
    kafka.start(timeout=90)
    try:
        bootstrap_servers = kafka.get_bootstrap_server()
        kafka_producer.configure(bootstrap_servers)
        kafka_consumer.configure(bootstrap_servers)
        yield
    finally:
        kafka.stop()


@pytest.fixture(autouse=True)
async def _reset_state() -> None:
    """DB rows are truncated between tests; Redis is flushed wholesale (it holds nothing
    that needs to survive a test — geo-index, availability set, surge cache, rate-limit
    counters all reset cleanly with one FLUSHDB, mirroring the blunt TRUNCATE approach above)."""
    async with db_engine.get_sessionmaker()() as session:
        await session.execute(text("TRUNCATE users, drivers, rides, ratings RESTART IDENTITY CASCADE"))
        await session.commit()
    await redis_client.get_redis_client().flushdb()


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
