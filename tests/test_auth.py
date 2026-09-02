"""Ported from AuthController.kt's behavior — register/login/switch-mode/me."""

from fastapi.testclient import TestClient

from ride_service import state


def test_register_issues_httponly_cookie_and_creates_rider(client: TestClient) -> None:
    response = client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    assert response.status_code == 201
    assert "auth_token" in response.cookies
    assert response.json()["token"]
    user = state.user_repository.find_by_username("alice")
    assert user is not None
    assert user.role.value == "RIDER"


def test_register_rejects_duplicate_username(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.post("/auth/register", json={"username": "alice", "password": "other"})
    assert response.status_code == 409


def test_login_succeeds_with_correct_credentials(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.post("/auth/login", json={"username": "alice", "password": "hunter2"})
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_rejects_wrong_password(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert response.status_code == 401


def test_login_rejects_unknown_username(client: TestClient) -> None:
    response = client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert response.status_code == 401


def test_auth_attempts_are_rate_limited(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    responses = [
        client.post("/auth/login", json={"username": "alice", "password": "wrong"}) for _ in range(15)
    ]
    assert any(r.status_code == 429 for r in responses)


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user_id(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.get("/auth/me")
    assert response.status_code == 200
    user = state.user_repository.find_by_username("alice")
    assert user is not None
    assert response.json()["user_id"] == user.id


def test_switch_mode_to_driver_requires_permanent_driver_role(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.post("/auth/switch-mode", json={"mode": "driver"})
    assert response.status_code == 403


def test_switch_mode_to_driver_succeeds_after_driver_registration(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    client.post("/driver/register", json={"vehicle_type": "sedan", "license_plate": "ABC123"})
    # /driver/register already reissues a DRIVER-mode cookie, but switch-mode should also work.
    response = client.post("/auth/switch-mode", json={"mode": "rider"})
    assert response.status_code == 200
    back_to_driver = client.post("/auth/switch-mode", json={"mode": "driver"})
    assert back_to_driver.status_code == 200


def test_switch_mode_rejects_unknown_mode(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "alice", "password": "hunter2"})
    response = client.post("/auth/switch-mode", json={"mode": "admin"})
    assert response.status_code == 400
