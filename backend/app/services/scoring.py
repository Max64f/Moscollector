"""Правильный скоринг по витрине тревог за последние 24 часа данных (не wall-clock)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import (
    HORIZON_HOURS,
    RECOMMENDATIONS,
    RISK_TITLES,
    RISKS,
    classify_event,
)
from app.models import Channel, EventAlarm, ObjectNode, Prediction


def data_horizon(db: Session) -> tuple[object, object] | None:
    max_ts = db.scalar(select(func.max(EventAlarm.event_ts)))
    if max_ts is None:
        return None
    return max_ts - timedelta(hours=HORIZON_HOURS), max_ts


def _seasonal_boost(risk: str, month: int) -> float:
    if risk == "flood" and month in {3, 4, 5, 6, 7, 8}:
        return 0.04
    if risk == "failure" and month in {11, 12, 1, 2}:
        return 0.03
    if risk == "fire" and month in {11, 12, 1, 2}:
        return 0.02
    return 0.0


def _proba(risk: str, n_events: int, n_channels: int, n_pickets: int, month: int | None = None) -> float:
    score = 0.52 + 0.08 * min(n_events, 6) + 0.05 * min(n_channels, 4) + 0.06 * min(n_pickets, 3)
    if risk == "intrusion" and n_channels <= 1:
        score = min(score, 0.68)
    if risk == "fire" and n_pickets >= 2:
        score = max(score, 0.78)
    if risk == "flood" and n_events >= 2:
        score = max(score, 0.74)
    if month:
        score += _seasonal_boost(risk, month)
    return round(min(0.97, score), 3)


def _explain(risk: str, n_events: int, n_channels: int, n_pickets: int, samples: list[str], month: int | None = None) -> str:
    title = RISK_TITLES[risk]
    bits = [f"{title} на 24ч (правила по журналу тревог)."]
    bits.append(f"событий: {n_events}, каналов: {n_channels}.")
    if n_pickets:
        bits.append(f"пикетов: {n_pickets}.")
    if risk == "intrusion" and n_channels <= 1:
        bits.append("одиночная сработка — чаще ложная, сверить охрану.")
    if month and _seasonal_boost(risk, month):
        if risk == "flood":
            bits.append("сезон паводков/ливней.")
        elif month in {11, 12, 1, 2}:
            bits.append("отопительный сезон.")
    if samples:
        bits.append("примеры: " + "; ".join(samples[:3]) + ".")
    return " ".join(bits)


def collect_risks(db: Session, object_id: int | None = None) -> dict[int, dict[str, dict]]:
    window = data_horizon(db)
    if window is None:
        return {}
    start, end = window
    stmt = (
        select(EventAlarm, Channel)
        .join(Channel, Channel.id == EventAlarm.channel_id)
        .where(EventAlarm.event_ts >= start, EventAlarm.event_ts <= end)
    )
    if object_id is not None:
        stmt = stmt.where(Channel.object_id == object_id)
    grouped: dict[int, dict[str, dict]] = defaultdict(
        lambda: {
            risk: {"n_events": 0, "channels": set(), "pickets": set(), "samples": []}
            for risk in RISKS
        }
    )
    for alarm, channel in db.execute(stmt):
        if channel.object_id is None:
            continue
        risk = classify_event(channel.sensor_type, alarm.value_cat)
        if risk is None:
            continue
        bucket = grouped[int(channel.object_id)][risk]
        bucket["n_events"] += 1
        bucket["channels"].add(channel.id)
        if channel.picket:
            bucket["pickets"].add(channel.picket)
        if len(bucket["samples"]) < 4:
            ts = alarm.event_ts.isoformat(timespec="minutes")
            bucket["samples"].append(f"{channel.sensor_type}={alarm.value_cat or alarm.value_raw} @ {ts}")
    return grouped


def upsert_rule_predictions(db: Session, object_id: int | None = None) -> list[Prediction]:
    grouped = collect_risks(db, object_id=object_id)
    if object_id is None:
        stale = db.scalars(
            select(Prediction).where(Prediction.status == "open", Prediction.source == "rules")
        ).all()
    else:
        stale = db.scalars(
            select(Prediction).where(
                Prediction.status == "open",
                Prediction.source == "rules",
                Prediction.object_id == object_id,
            )
        ).all()
    for row in stale:
        db.delete(row)
    db.flush()
    created: list[Prediction] = []
    window = data_horizon(db)
    month = window[1].month if window else None
    for oid, risks in grouped.items():
        for risk, bucket in risks.items():
            n_events = bucket["n_events"]
            if n_events <= 0:
                continue
            n_channels = len(bucket["channels"])
            n_pickets = len(bucket["pickets"])
            row = Prediction(
                object_id=oid,
                channel_id=next(iter(bucket["channels"]), None),
                risk_type=risk,
                probability=_proba(risk, n_events, n_channels, n_pickets, month),
                horizon_hours=HORIZON_HOURS,
                explanation=_explain(risk, n_events, n_channels, n_pickets, bucket["samples"], month),
                recommendation=RECOMMENDATIONS[risk],
                status="open",
                source="rules",
            )
            db.add(row)
            created.append(row)
    db.flush()
    return created


def serialize_prediction(row: Prediction, names: dict[int, str] | None = None) -> dict:
    object_name = None
    if row.object_id is not None and names is not None:
        object_name = names.get(int(row.object_id))
    return {
        "id": row.id,
        "object_id": row.object_id,
        "object_name": object_name,
        "channel_id": row.channel_id,
        "risk_type": row.risk_type,
        "risk_title": RISK_TITLES.get(row.risk_type, row.risk_type),
        "probability": row.probability,
        "horizon_hours": row.horizon_hours,
        "explanation": row.explanation,
        "recommendation": row.recommendation,
        "status": row.status,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def object_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.scalars(select(ObjectNode).where(ObjectNode.id.in_(ids))).all()
    return {int(row.id): row.name for row in rows}
