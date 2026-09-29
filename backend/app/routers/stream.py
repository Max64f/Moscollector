"""Имитатор near-real-time: реплей тревог СМВУ и пересчёт правил < 5 мин."""

from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.db import get_db
from app.domain import classify_event
from app.models import Channel, EventAlarm
from app.services.audit import write_audit
from app.services.scoring import data_horizon, object_names, serialize_prediction, upsert_rule_predictions

router = APIRouter(tags=["stream"])

MAX_LAG_SECONDS = 5 * 60


@router.get("/stream/status")
def stream_status(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("dashboard:read")),
) -> dict:
    window = data_horizon(db)
    n_alarms = db.scalar(select(func.count()).select_from(EventAlarm)) or 0
    return {
        "alarms": int(n_alarms),
        "data_to": window[1].isoformat() if window else None,
        "data_from": window[0].isoformat() if window else None,
        "slo_seconds": MAX_LAG_SECONDS,
        "note": "Пачка СМВУ пишет новые тревоги сразу после последнего timestamp витрины и пересчитывает правила.",
    }


@router.post("/stream/tick")
def stream_tick(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("integrations:write")),
    scenario: str = Query(default="mix", description="fire | flood | failure | mix"),
    n: int = Query(default=8, ge=1, le=40),
) -> dict:
    started = perf_counter()
    if scenario not in {"fire", "flood", "failure", "intrusion", "mix"}:
        raise HTTPException(status_code=400, detail="scenario=fire|flood|failure|intrusion|mix")
    wanted = None if scenario == "mix" else scenario
    stmt = (
        select(EventAlarm, Channel)
        .join(Channel, Channel.id == EventAlarm.channel_id)
        .order_by(EventAlarm.event_ts.desc())
        .limit(2000)
    )
    templates = []
    seen_channels: set[int] = set()
    for alarm, channel in db.execute(stmt):
        risk = classify_event(channel.sensor_type, alarm.value_cat)
        if risk is None:
            continue
        if wanted and risk != wanted:
            continue
        if channel.id in seen_channels:
            continue
        seen_channels.add(channel.id)
        templates.append((alarm, channel, risk))
        if len(templates) >= n:
            break
    if not templates:
        raise HTTPException(status_code=404, detail="нет шаблонов тревог под сценарий")

    max_ts = db.scalar(select(func.max(EventAlarm.event_ts)))
    next_id = int(db.scalar(select(func.coalesce(func.max(EventAlarm.event_id), 0))) or 0)
    stamp = (max_ts + timedelta(minutes=1)) if max_ts else datetime.utcnow()
    objects: set[int] = set()
    inserted = 0
    for alarm, channel, _risk in templates:
        next_id += 1
        db.add(
            EventAlarm(
                event_id=next_id,
                channel_id=channel.id,
                event_ts=stamp,
                value_raw=alarm.value_raw,
                value_num=alarm.value_num,
                value_cat=alarm.value_cat,
            )
        )
        inserted += 1
        if channel.object_id is not None:
            objects.add(int(channel.object_id))
    db.flush()
    created = []
    for oid in objects:
        created.extend(upsert_rule_predictions(db, object_id=oid))
    latency_ms = round((perf_counter() - started) * 1000, 1)
    write_audit(
        db,
        user.login,
        "stream.tick",
        {
            "scenario": scenario,
            "inserted": inserted,
            "objects": list(objects),
            "latency_ms": latency_ms,
        },
    )
    db.commit()
    names = object_names(db, objects)
    return {
        "scenario": scenario,
        "inserted": inserted,
        "objects": sorted(objects),
        "rules_written": len(created),
        "latency_ms": latency_ms,
        "within_slo": latency_ms < MAX_LAG_SECONDS * 1000,
        "event_ts": stamp.isoformat() if stamp else None,
        "predictions": [serialize_prediction(row, names) for row in created],
        "detail": "Имитатор СМВУ: реплей витрины, не живой контур. ТЗ: вывод < 5 мин.",
    }
