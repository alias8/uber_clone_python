"""Ported from uber_clone's AuthController.kt."""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ride_service.auth.cookies import issue_token_cookie
from ride_service.auth.dependencies import AuthContext, get_current_auth
from ride_service.models import Role, User
from ride_service.schemas import AuthResponse, LoginRequest, MeResponse, RegisterRequest, SwitchModeRequest
from ride_service.state import auth_attempt_rate_limiter, user_repository

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, http_request: Request, response: Response) -> AuthResponse:
    if not auth_attempt_rate_limiter.allow(_client_ip(http_request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — try again later")
    if await user_repository.exists_by_username(request.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    user = await user_repository.save(
        User(username=request.username, password_hash=_hash_password(request.password))
    )
    token = issue_token_cookie(response, user.username, Role.RIDER)
    return AuthResponse(token=token)


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, http_request: Request, response: Response) -> AuthResponse:
    if not auth_attempt_rate_limiter.allow(_client_ip(http_request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — try again later")

    user = await user_repository.find_by_username(request.username)
    if user is None or not _verify_password(request.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = issue_token_cookie(response, user.username, user.role)
    return AuthResponse(token=token)


@router.post("/switch-mode", response_model=AuthResponse)
async def switch_mode(
    request: SwitchModeRequest, response: Response, auth: AuthContext = Depends(get_current_auth)
) -> AuthResponse:
    try:
        requested_mode = Role(request.mode.upper())
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid mode") from None

    # Switching *into* driver mode requires the permanent DB role to already be DRIVER —
    # switching back to RIDER mode is always allowed, matching SwitchModeRequest's original
    # behavior of only gating the DRIVER branch.
    if requested_mode == Role.DRIVER:
        user = await user_repository.find_by_username(auth.username)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
        if user.role != Role.DRIVER:
            raise HTTPException(status.HTTP_403_FORBIDDEN)

    token = issue_token_cookie(response, auth.username, requested_mode)
    return AuthResponse(token=token)


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    user = await user_repository.find_by_username(auth.username)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return MeResponse(user_id=user.id)
