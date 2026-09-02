"""Ported from uber_clone's RideController.kt."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ride_service.auth.dependencies import AuthContext, get_current_auth, require_role, resolve_current_user
from ride_service.models import Role, User
from ride_service.schemas import RatingRequest, RideRequest, RideResponse, ride_to_response
from ride_service.state import rating_service, ride_repository, ride_request_rate_limiter, ride_service

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post("", response_model=RideResponse, status_code=status.HTTP_201_CREATED)
def request_ride(
    request: RideRequest,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.RIDER)),
) -> RideResponse:
    # Prevents rider from requesting, then cancelling rapidly.
    if not ride_request_rate_limiter.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many ride requests — try again shortly")
    ride = ride_service.request_ride(
        user.id, request.pickup_lat, request.pickup_lng, request.dropoff_lat, request.dropoff_lng
    )
    return ride_to_response(ride)


@router.get("/history", response_model=list[RideResponse])
def history(user: User = Depends(resolve_current_user)) -> list[RideResponse]:
    rides = ride_repository.find_by_rider_id_ordered(user.id)
    return [ride_to_response(r) for r in rides]


@router.get("/{ride_id}", response_model=RideResponse)
def get_ride(ride_id: str, _auth: AuthContext = Depends(get_current_auth)) -> RideResponse:
    return ride_to_response(ride_service.get_ride(ride_id))


@router.post("/{ride_id}/accept", response_model=RideResponse)
def accept_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(ride_service.accept_ride(ride_id, user.id))


@router.post("/{ride_id}/start", response_model=RideResponse)
def start_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(ride_service.start_ride(ride_id, user.id))


@router.post("/{ride_id}/complete", response_model=RideResponse)
def complete_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(ride_service.complete_ride(ride_id, user.id))


@router.post("/{ride_id}/cancel", response_model=RideResponse)
def cancel_ride(ride_id: str, user: User = Depends(resolve_current_user)) -> RideResponse:
    return ride_to_response(ride_service.cancel_ride(ride_id, user.id))


@router.post("/{ride_id}/rate", status_code=status.HTTP_204_NO_CONTENT)
def rate_ride(ride_id: str, request: RatingRequest, user: User = Depends(resolve_current_user)) -> None:
    rating_service.rate(ride_id, user.id, request.score, request.comment)
