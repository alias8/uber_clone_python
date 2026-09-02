from __future__ import annotations

from sqlalchemy import exists, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import UserRow
from ride_service.models import Role, User


def _user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        role=Role(row.role),
        avg_rating=row.avg_rating,
        rating_count=row.rating_count,
    )


def _user_to_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        username=user.username,
        password_hash=user.password_hash,
        role=user.role.value,
        avg_rating=user.avg_rating,
        rating_count=user.rating_count,
    )


class UserRepository:
    async def save(self, user: User) -> User:
        async with get_sessionmaker()() as session:
            merged = await session.merge(_user_to_row(user))
            await session.commit()
            return _user_from_row(merged)

    async def find_by_id(self, user_id: str) -> User | None:
        async with get_sessionmaker()() as session:
            row = await session.get(UserRow, user_id)
            return _user_from_row(row) if row is not None else None

    async def find_by_username(self, username: str) -> User | None:
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
            return _user_from_row(row) if row is not None else None

    async def exists_by_username(self, username: str) -> bool:
        async with get_sessionmaker()() as session:
            return bool(
                (
                    await session.execute(select(exists().where(UserRow.username == username)))
                ).scalar_one()
            )
