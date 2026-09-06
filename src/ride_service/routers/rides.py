"""Ported from uber_clone's RideController.kt (and RideLocationController.kt for the SSE
endpoint)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from ride_service import sse
from ride_service.auth.dependencies import AuthContext, get_current_auth, require_role, resolve_current_user
from ride_service.models import RideStatus, Role, User
from ride_service.schemas import RatingRequest, RideRequest, RideResponse, ride_to_response
from ride_service.state import rating_service, ride_repository, ride_request_rate_limiter, ride_service

_TRACKABLE_STATUSES = (RideStatus.MATCHED, RideStatus.IN_PROGRESS)

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post("", response_model=RideResponse, status_code=status.HTTP_201_CREATED)
async def request_ride(
    request: RideRequest, # Pydantic BaseModel RideRequest
    user: User = Depends(resolve_current_user), # fastapi Depends
    _auth: AuthContext = Depends(require_role(Role.RIDER)),
) -> RideResponse:
    # Prevents rider from requesting, then cancelling rapidly.
    if not await ride_request_rate_limiter.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many ride requests — try again shortly")
    ride = await ride_service.request_ride(
        user.id, request.pickup_lat, request.pickup_lng, request.dropoff_lat, request.dropoff_lng
    )
    return ride_to_response(ride)


@router.get("/history", response_model=list[RideResponse])
async def history(user: User = Depends(resolve_current_user)) -> list[RideResponse]:
    rides = await ride_repository.find_by_rider_id_ordered(user.id)
    return [ride_to_response(r) for r in rides]


@router.get("/{ride_id}", response_model=RideResponse)
async def get_ride(ride_id: str, _auth: AuthContext = Depends(get_current_auth)) -> RideResponse:
    return ride_to_response(await ride_service.get_ride(ride_id))


@router.post("/{ride_id}/accept", response_model=RideResponse)
async def accept_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(await ride_service.accept_ride(ride_id, user.id))


@router.post("/{ride_id}/start", response_model=RideResponse)
async def start_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(await ride_service.start_ride(ride_id, user.id))


@router.post("/{ride_id}/complete", response_model=RideResponse)
async def complete_ride(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.DRIVER)),
) -> RideResponse:
    return ride_to_response(await ride_service.complete_ride(ride_id, user.id))


@router.post("/{ride_id}/cancel", response_model=RideResponse)
async def cancel_ride(ride_id: str, user: User = Depends(resolve_current_user)) -> RideResponse:
    return ride_to_response(await ride_service.cancel_ride(ride_id, user.id))


@router.post("/{ride_id}/rate", status_code=status.HTTP_204_NO_CONTENT)
async def rate_ride(
    ride_id: str, request: RatingRequest, user: User = Depends(resolve_current_user)
) -> None:
    await rating_service.rate(ride_id, user.id, request.score, request.comment)


@router.get("/{ride_id}/location")
async def stream_driver_location(
    ride_id: str,
    user: User = Depends(resolve_current_user),
    _auth: AuthContext = Depends(require_role(Role.RIDER)),
) -> StreamingResponse:
    ride = await ride_service.get_ride(ride_id)
    if ride.rider_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    if ride.status not in _TRACKABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "No active driver to track for this ride")
    return StreamingResponse(sse.stream(ride_id), media_type="text/event-stream")
