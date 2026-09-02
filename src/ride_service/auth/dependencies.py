"""Ported from uber_clone's JwtFilter.kt + the `@PreAuthorize("hasRole(...)")` guards on each
controller. Every authenticated user always holds RIDER authority; DRIVER authority is granted
additionally only while the token's active-mode `role` claim is DRIVER — so a user in driver
mode can still call rider endpoints, matching the Kotlin filter's behavior exactly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from ride_service.auth.jwt import extract_role, extract_username, is_valid
from ride_service.models import Role, User
from ride_service.state import user_repository


@dataclass
class AuthContext:
    username: str
    authorities: frozenset[Role]


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        return header.removeprefix("Bearer ")
    return request.cookies.get("auth_token")


def get_current_auth(request: Request) -> AuthContext:
    token = _extract_token(request)
    if token is None or not is_valid(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    username = extract_username(token)
    active_role = extract_role(token) or Role.RIDER
    authorities = {Role.RIDER}
    if active_role == Role.DRIVER:
        authorities.add(Role.DRIVER)
    return AuthContext(username=username, authorities=frozenset(authorities))


def require_role(role: Role) -> Callable[[AuthContext], AuthContext]:
    def _dependency(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if role not in auth.authorities:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires active {role.value} mode")
        return auth

    return _dependency


def resolve_current_user(auth: AuthContext = Depends(get_current_auth)) -> User:
    user = user_repository.find_by_username(auth.username)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user
