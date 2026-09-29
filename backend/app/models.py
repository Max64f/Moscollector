from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ObjectNode(Base):
    __tablename__ = "objects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("objects.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    channels: Mapped[list["Channel"]] = relationship(back_populates="object")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    system_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sensor_type: Mapped[str] = mapped_column(String(128), nullable=False)
    tag: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("objects.id"))
    picket: Mapped[str | None] = mapped_column(String(64))

    object: Mapped[ObjectNode | None] = relationship(back_populates="channels")


class EventAlarm(Base):
    __tablename__ = "events_alarm"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    value_raw: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_cat: Mapped[str | None] = mapped_column(String(255))


class EventNumericHourly(Base):
    __tablename__ = "events_numeric_hourly"
    __table_args__ = (UniqueConstraint("channel_id", "hour_ts", name="uq_hourly_channel_hour"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    hour_ts: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    n_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_alarms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    value_mean: Mapped[float | None] = mapped_column(Float)
    value_min: Mapped[float | None] = mapped_column(Float)
    value_max: Mapped[float | None] = mapped_column(Float)


class MeteoDaily(Base):
    __tablename__ = "meteo_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    temp_mean: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    risk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    explanation: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="lgbm")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DispatcherFeedback(Base):
    __tablename__ = "dispatcher_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("predictions.id"))
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    user_login: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TicketDraft(Base):
    __tablename__ = "ticket_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("predictions.id"))
    object_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    external_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelCalibration(Base):
    __tablename__ = "model_calibration"

    risk_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    n_dispatch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_false_alarm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_login: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_login: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
