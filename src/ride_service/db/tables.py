"""SQLAlchemy 2.0 ORM tables. Column-for-column port of uber_clone's
src/main/resources/db/migration/V1__baseline_schema.sql (plus the V2 indexes and the V3
rating_count columns — see migrations/versions/) — table and column names match the Kotlin
schema exactly so the two backends stay directly comparable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    avg_rating: Mapped[float | None] = mapped_column(Double, nullable=True)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DriverRow(Base):
    __tablename__ = "drivers"

    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), primary_key=True)
    vehicle_type: Mapped[str] = mapped_column(String(255), nullable=False)
    license_plate: Mapped[str] = mapped_column(String(255), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    avg_rating: Mapped[float | None] = mapped_column(Double, nullable=True)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RideRow(Base):
    __tablename__ = "rides"
    __table_args__ = (
        Index("idx_rides_rider_requested", "rider_id", "requested_at"),
        Index("idx_rides_driver_requested", "driver_id", "requested_at"),
        Index("idx_rides_driver_status", "driver_id", "status"),
        Index("idx_rides_status_location", "status", "pickup_lat", "pickup_lng"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    driver_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pickup_lat: Mapped[float] = mapped_column(Double, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Double, nullable=False)
    dropoff_lat: Mapped[float] = mapped_column(Double, nullable=False)
    dropoff_lng: Mapped[float] = mapped_column(Double, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    estimated_fare: Mapped[Decimal | None] = mapped_column(Numeric(19, 2), nullable=True)
    fare: Mapped[Decimal | None] = mapped_column(Numeric(19, 2), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Carried through as a plain counter, not SQLAlchemy's version_id_col optimistic-locking —
    # RideService._accept_lock (an asyncio.Lock) is what makes concurrent accepts safe here,
    # same deliberate deviation from JPA's @Version already documented in services.py.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class RatingRow(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("ride_id", "from_user_id", name="uq_ratings_ride_from"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ride_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    to_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
