"""Ported from RideService.kt's state machine and RideController.kt's role guards."""

from fastapi.testclient import TestClient

from ride_service.main import app
from tests.conftest import register_and_login, register_driver

RIDE_REQUEST = {
    "pickup_lat": 40.7128,
    "pickup_lng": -74.0060,
    "dropoff_lat": 40.7300,
    "dropoff_lng": -74.0000,
}


def _new_client() -> TestClient:
    return TestClient(app)


def _request_ride(client: TestClient) -> dict[str, object]:
    response = client.post("/rides", json=RIDE_REQUEST)
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


def test_rider_can_request_a_ride_and_gets_an_estimated_fare(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)
    assert ride["status"] == "REQUESTED"
    assert ride["estimated_fare"] is not None
    assert ride["fare"] is None
    estimated_minutes = ride["estimated_journey_minutes"]
    assert isinstance(estimated_minutes, int)
    assert estimated_minutes >= 1


def test_a_user_in_driver_mode_can_still_request_a_ride(client: TestClient) -> None:
    # Ported from JwtFilter.kt: every authenticated user always holds RIDER authority, and
    # DRIVER authority is granted additionally on top of it — driver mode never revokes it.
    register_driver(client, "driver1")
    response = client.post("/rides", json=RIDE_REQUEST)
    assert response.status_code == 201


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.post("/rides", json=RIDE_REQUEST)
    assert response.status_code == 401


def test_rider_cannot_have_two_active_rides(client: TestClient) -> None:
    register_and_login(client, "rider1")
    _request_ride(client)
    response = client.post("/rides", json=RIDE_REQUEST)
    assert response.status_code == 409


def test_ride_requests_are_rate_limited(client: TestClient) -> None:
    register_and_login(client, "rider1")
    _request_ride(client)  # consumes 1 of the 5/min allowance
    # Cancel then re-request repeatedly to hit the per-rider ride-request limiter (5/min)
    # without tripping the separate "rider already has an active ride" 409 first.
    responses = []
    for _ in range(6):
        ride = client.get("/rides/history").json()[0]
        client.post(f"/rides/{ride['id']}/cancel")
        responses.append(client.post("/rides", json=RIDE_REQUEST))
    assert any(r.status_code == 429 for r in responses)


def test_full_happy_path_request_accept_start_complete_rate(client: TestClient) -> None:
    rider = client
    register_and_login(rider, "rider1")
    ride = _request_ride(rider)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})

    accept = driver.post(f"/rides/{ride['id']}/accept")
    assert accept.status_code == 200
    assert accept.json()["status"] == "MATCHED"
    assert accept.json()["driver_id"] is not None

    start = driver.post(f"/rides/{ride['id']}/start")
    assert start.status_code == 200
    assert start.json()["status"] == "IN_PROGRESS"

    complete = driver.post(f"/rides/{ride['id']}/complete")
    assert complete.status_code == 200
    assert complete.json()["status"] == "COMPLETED"
    assert complete.json()["fare"] is not None

    rate_by_rider = rider.post(f"/rides/{ride['id']}/rate", json={"score": 5, "comment": "great ride"})
    assert rate_by_rider.status_code == 204

    # Can't rate the same ride twice from the same participant.
    again = rider.post(f"/rides/{ride['id']}/rate", json={"score": 4, "comment": None})
    assert again.status_code == 409


def test_accept_requires_driver_role(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)
    response = client.post(f"/rides/{ride['id']}/accept")
    assert response.status_code == 403


def test_accept_fails_without_a_driver_profile(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    other = _new_client()
    register_and_login(other, "wannabe-driver")
    # No driver profile registered, and not in driver mode — role guard rejects first.
    response = other.post(f"/rides/{ride['id']}/accept")
    assert response.status_code == 403


def test_accept_fails_when_ride_already_matched(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver1 = _new_client()
    register_driver(driver1, "driver1")
    driver1.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    assert driver1.post(f"/rides/{ride['id']}/accept").status_code == 200

    driver2 = _new_client()
    register_driver(driver2, "driver2")
    driver2.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    response = driver2.post(f"/rides/{ride['id']}/accept")
    assert response.status_code == 409


def test_accept_fails_when_driver_not_available(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    # Never went online (mode/on), so is_available is still False.
    response = driver.post(f"/rides/{ride['id']}/accept")
    assert response.status_code == 409


def test_start_requires_matched_status(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})

    response = driver.post(f"/rides/{ride['id']}/start")
    assert response.status_code == 409


def test_start_requires_the_assigned_driver(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver1 = _new_client()
    register_driver(driver1, "driver1")
    driver1.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver1.post(f"/rides/{ride['id']}/accept")

    driver2 = _new_client()
    register_driver(driver2, "driver2")
    driver2.post("/driver/mode/on", json={"lat": 40.8, "lng": -74.1})
    response = driver2.post(f"/rides/{ride['id']}/start")
    assert response.status_code == 403


def test_complete_requires_in_progress_status(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver.post(f"/rides/{ride['id']}/accept")

    response = driver.post(f"/rides/{ride['id']}/complete")
    assert response.status_code == 409


def test_cancel_allowed_for_rider_before_in_progress(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)
    response = client.post(f"/rides/{ride['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancel_rejected_once_in_progress(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver.post(f"/rides/{ride['id']}/accept")
    driver.post(f"/rides/{ride['id']}/start")

    response = client.post(f"/rides/{ride['id']}/cancel")
    assert response.status_code == 409


def test_cancel_rejected_for_non_participant(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    stranger = _new_client()
    register_and_login(stranger, "stranger")
    response = stranger.post(f"/rides/{ride['id']}/cancel")
    assert response.status_code == 403


def test_cancelling_a_matched_ride_frees_the_driver(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver.post(f"/rides/{ride['id']}/accept")

    client.post(f"/rides/{ride['id']}/cancel")

    profile = driver.get("/driver/profile")
    assert profile.json()["is_available"] is True


def test_get_ride_requires_authentication(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    anon = _new_client()
    response = anon.get(f"/rides/{ride['id']}")
    assert response.status_code == 401


def test_get_unknown_ride_returns_404(client: TestClient) -> None:
    register_and_login(client, "rider1")
    response = client.get("/rides/does-not-exist")
    assert response.status_code == 404


def test_rate_out_of_range_score_is_rejected(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)

    driver = _new_client()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", json={"lat": 40.7128, "lng": -74.0060})
    driver.post(f"/rides/{ride['id']}/accept")
    driver.post(f"/rides/{ride['id']}/start")
    driver.post(f"/rides/{ride['id']}/complete")

    response = client.post(f"/rides/{ride['id']}/rate", json={"score": 6, "comment": None})
    assert response.status_code == 400


def test_rate_requires_a_completed_ride(client: TestClient) -> None:
    register_and_login(client, "rider1")
    ride = _request_ride(client)
    response = client.post(f"/rides/{ride['id']}/rate", json={"score": 5, "comment": None})
    assert response.status_code == 409
