"""Ported from uber_clone's JwtUtil.kt. The `role` claim is the *active mode* for this session
(reissued on /auth/switch-mode), not necessarily the user's permanent DB role — see
auth/dependencies.py for how the two interact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from ride_service.config import settings
from ride_service.models import Role

ALGORITHM = "HS256"


def generate(username: str, role: Role) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(milliseconds=settings.jwt_expiration_ms),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def extract_username(token: str) -> str:
    payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    username = payload["sub"]
    assert isinstance(username, str)
    return username


def extract_role(token: str) -> Role | None:
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return Role(payload["role"])
    except (pyjwt.InvalidTokenError, ValueError, KeyError):
        return None


def is_valid(token: str) -> bool:
    try:
        extract_username(token)
        return True
    except pyjwt.InvalidTokenError:
        return False
