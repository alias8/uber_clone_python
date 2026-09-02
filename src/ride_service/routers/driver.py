"""Ported from uber_clone's DriverController.kt."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Response, status

from ride_service.auth.cookies import issue_token_cookie
from ride_service.auth.dependencies import AuthContext, require_role, resolve_current_user
from ride_service.dispatch import DEFAULT_SEARCH_RADIUS_KM
from ride_service.models import Role, User
from ride_service.schemas import (
    DriverLocationRequest,
    DriverProfileResponse,
    DriverRegisterRequest,
    NearbyDriverResponse,
    RideResponse,
    driver_to_response,
    ride_to_response,
)
from ride_service.state import driver_service, ride_repository, user_repository

router = APIRouter(prefix="/driver", tags=["driver"])


@router.post("/register", response_model=DriverProfileResponse, status_code=status.HTTP_201_CREATED)
def register(
    request: DriverRegisterRequest, response: Response, user: User = Depends(resolve_current_user)
) -> DriverProfileResponse:
    driver = driver_service.register_driver(user.id, request.vehicle_type, request.license_plate)
    user_repository.save(replace(user, role=Role.DRIVER))
    issue_token_cookie(response, user.username, Role.DRIVER)
    return driver_to_response(driver)


@router.get("/profile", response_model=DriverProfileResponse)
def profile(
    user: User = Depends(resolve_current_user), _auth: AuthContext = Depends(require_role(Role.DRIVER))
) -> DriverProfileResponse:
    return driver_to_response(driver_service.get_profile(user.id))


@router.post("/mode/on", response_model=DriverProfileResponse)
def go_online(
    request: DriverLocationRequest,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> DriverProfileResponse:
    return driver_to_response(driver_service.go_online(user.id, request.lat, request.lng))


@router.post("/mode/off", response_model=DriverProfileResponse)
def go_offline(
    user: User = Depends(resolve_current_user), _auth: AuthContext = Depends(require_role(Role.DRIVER))
) -> DriverProfileResponse:
    return driver_to_response(driver_service.go_offline(user.id))


@router.post("/location", status_code=status.HTTP_204_NO_CONTENT)
def update_location(
    request: DriverLocationRequest,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> None:
    driver_service.update_location(user.id, request.lat, request.lng)


@router.get("/rides", response_model=list[RideResponse])
def ride_history(
    user: User = Depends(resolve_current_user), _auth: AuthContext = Depends(require_role(Role.DRIVER))
) -> list[RideResponse]:
    rides = ride_repository.find_by_driver_id_ordered(user.id)
    return [ride_to_response(r) for r in rides]


@router.get("/nearby", response_model=list[NearbyDriverResponse])
def nearby(
    lat: float,
    lng: float,
    radius_km: float = DEFAULT_SEARCH_RADIUS_KM,
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> list[NearbyDriverResponse]:
    results = driver_service.find_nearby(lat, lng, radius_km)
    return [NearbyDriverResponse(driver_id=r.driver_id, distance_km=r.distance_km) for r in results]
