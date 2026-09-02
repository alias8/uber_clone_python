"""Ported from DriverController.kt / DriverService.kt."""

from fastapi.testclient import TestClient

from ride_service.main import app
from tests.conftest import register_and_login, register_driver


def _new_client() -> TestClient:
    return TestClient(app)


def test_register_driver_upgrades_permanent_role_and_reissues_driver_cookie(client: TestClient) -> None:
    register_and_login(client, "alice")
    response = client.post("/driver/register", json={"vehicle_type": "sedan", "license_plate": "ABC123"})
    assert response.status_code == 201
    assert response.json()["is_available"] is False

    # DRIVER-mode cookie was reissued, so a DRIVER-only endpoint now works without a fresh login.
    profile = client.get("/driver/profile")
    assert profile.status_code == 200


def test_cannot_register_as_driver_twice(client: TestClient) -> None:
    register_driver(client, "alice")
    response = client.post("/driver/register", json={"vehicle_type": "suv", "license_plate": "XYZ999"})
    assert response.status_code == 409


def test_driver_only_endpoints_reject_rider_mode(client: TestClient) -> None:
    register_and_login(client, "alice")
    response = client.get("/driver/profile")
    assert response.status_code == 403


def test_go_online_sets_available_and_location(client: TestClient) -> None:
    register_driver(client, "alice")
    response = client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    assert response.status_code == 200
    assert response.json()["is_available"] is True


def test_go_offline_clears_availability(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    response = client.post("/driver/mode/off")
    assert response.status_code == 200
    assert response.json()["is_available"] is False


def test_update_location_requires_a_driver_profile_and_driver_mode(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    response = client.post("/driver/location", json={"lat": 40.72, "lng": -74.01})
    assert response.status_code == 204


def test_nearby_returns_only_available_drivers_within_radius(client: TestClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})

    far_driver = _new_client()
    register_driver(far_driver, "bob")
    # ~3900km away (LA) — well outside any reasonable radius.
    far_driver.post("/driver/mode/on", json={"lat": 34.0522, "lng": -118.2437})

    offline_driver = _new_client()
    register_driver(offline_driver, "carol")
    # Registered but never went online — not available, shouldn't show up.

    response = client.get("/driver/nearby", params={"lat": 40.7128, "lng": -74.0060, "radius_km": 5.0})
    assert response.status_code == 200
    driver_ids = [d["driver_id"] for d in response.json()]

    alice = client.get("/auth/me").json()["user_id"]
    assert alice in driver_ids
    assert len(driver_ids) == 1


def test_driver_ride_history_only_shows_own_rides(client: TestClient) -> None:
    rider = _new_client()
    register_and_login(rider, "rider1")
    ride = rider.post(
        "/rides",
        json={"pickup_lat": 40.7128, "pickup_lng": -74.0060, "dropoff_lat": 40.73, "dropoff_lng": -74.0},
    ).json()

    register_driver(client, "alice")
    client.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    client.post(f"/rides/{ride['id']}/accept")

    other_driver = _new_client()
    register_driver(other_driver, "bob")

    history = client.get("/driver/rides")
    assert history.status_code == 200
    assert len(history.json()) == 1

    other_history = other_driver.get("/driver/rides")
    assert other_history.status_code == 200
    assert other_history.json() == []
