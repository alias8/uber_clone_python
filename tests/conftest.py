from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ride_service import state
from ride_service.main import app


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """All state lives in process-wide singletons for M1 — reset them between tests."""
    state.user_repository.clear()
    state.driver_repository.clear()
    state.ride_repository.clear()
    state.rating_repository.clear()
    state.pricing_service.clear_cache()
    state.ride_request_rate_limiter.clear()
    state.auth_attempt_rate_limiter.clear()
    yield


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
