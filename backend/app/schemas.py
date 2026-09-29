from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain import DECISIONS


class UserOut(BaseModel):
    login: str
    role: str
    display_name: str


class ObjectOut(BaseModel):
    id: int
    level: int
    parent_id: int | None
    kind: str
    name: str
    open_risks: dict[str, int] = Field(default_factory=dict)
    max_probability: float | None = None


class ChannelOut(BaseModel):
    id: int
    sensor_type: str
    system_type: str
    name: str
    picket: str | None
    tag: str | None = None


class EventOut(BaseModel):
    event_id: int
    event_ts: str
    channel_id: int
    object_id: int | None
    object_name: str | None = None
    sensor_type: str | None
    value_raw: str | None
    value_cat: str | None
    risk: str | None = None


class PredictionOut(BaseModel):
    id: int
    object_id: int | None
    object_name: str | None = None
    channel_id: int | None
    risk_type: str
    risk_title: str
    probability: float
    horizon_hours: int
    explanation: str | None
    recommendation: str | None
    status: str
    source: str
    created_at: str | None = None


class DecisionIn(BaseModel):
    decision: str = Field(examples=list(DECISIONS))
    reason: str | None = None
    create_ticket: bool = False


class TicketIn(BaseModel):
    prediction_id: int | None = None
    object_id: int | None = None
    title: str | None = None
    body: str | None = None


class TicketOut(BaseModel):
    id: int
    prediction_id: int | None
    object_id: int | None
    title: str
    body: str | None
    status: str
    external_id: str | None = None
    created_at: str | None = None


class SmvuEventIn(BaseModel):
    event_id: int | None = None
    channel_id: int
    event_ts: datetime | None = None
    alarm: bool = True
    value: str | None = None


class SmvuBatchIn(BaseModel):
    events: list[SmvuEventIn]


class PredictRunIn(BaseModel):
    object_id: int | None = None
    replace_open_rules: bool = True
