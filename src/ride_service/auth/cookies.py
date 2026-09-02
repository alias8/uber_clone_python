"""Ported from uber_clone's JwtCookieService.kt."""

from fastapi import Response

from ride_service.auth.jwt import generate
from ride_service.config import settings
from ride_service.models import Role

COOKIE_NAME = "auth_token"


def issue_token_cookie(response: Response, username: str, role: Role) -> str:
    token = generate(username, role)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        path="/",
        max_age=settings.jwt_expiration_ms // 1000,
    )
    return token
