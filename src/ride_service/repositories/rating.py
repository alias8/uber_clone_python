from __future__ import annotations

from sqlalchemy import exists, select

from ride_service.db.engine import get_sessionmaker
from ride_service.db.tables import RatingRow
from ride_service.models import Rating


def _rating_from_row(row: RatingRow) -> Rating:
    return Rating(
        id=row.id,
        ride_id=row.ride_id,
        from_user_id=row.from_user_id,
        to_user_id=row.to_user_id,
        score=row.score,
        comment=row.comment,
        created_at=row.created_at,
    )


def _rating_to_row(rating: Rating) -> RatingRow:
    return RatingRow(
        id=rating.id,
        ride_id=rating.ride_id,
        from_user_id=rating.from_user_id,
        to_user_id=rating.to_user_id,
        score=rating.score,
        comment=rating.comment,
        created_at=rating.created_at,
    )


class RatingRepository:
    async def save(self, rating: Rating) -> Rating:
        async with get_sessionmaker()() as session:
            merged = await session.merge(_rating_to_row(rating))
            await session.commit()
            return _rating_from_row(merged)

    async def exists_by_ride_id_and_from_user_id(self, ride_id: str, from_user_id: str) -> bool:
        async with get_sessionmaker()() as session:
            stmt = exists().where(RatingRow.ride_id == ride_id, RatingRow.from_user_id == from_user_id)
            return bool((await session.execute(select(stmt))).scalar_one())
