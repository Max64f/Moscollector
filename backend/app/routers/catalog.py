from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.db import get_db
from app.domain import RISK_TITLES, classify_event
from app.models import Channel, EventAlarm, ObjectNode, Prediction
from app.services.scoring import data_horizon, object_names, serialize_prediction

router = APIRouter(tags=["catalog"])


def _picket_sort(value: str) -> tuple:
    digits = "".join(ch if ch.isdigit() else " " for ch in value).split()
    return (0, int(digits[0])) if digits else (1, 0)


def _risk_map(db: Session, object_ids: list[int] | None = None) -> dict[int, dict]:
    stmt = (
        select(
            Prediction.object_id,
            Prediction.risk_type,
            func.count(),
            func.max(Prediction.probability),
        )
        .where(Prediction.status == "open")
        .group_by(Prediction.object_id, Prediction.risk_type)
    )
    if object_ids:
        stmt = stmt.where(Prediction.object_id.in_(object_ids))
    grouped: dict[int, dict] = defaultdict(lambda: {"open_risks": {}, "max_probability": 0.0})
    for oid, risk, n, max_p in db.execute(stmt):
        if oid is None:
            continue
        grouped[int(oid)]["open_risks"][risk] = int(n)
        grouped[int(oid)]["max_probability"] = max(grouped[int(oid)]["max_probability"], float(max_p or 0))
    return grouped


@router.get("/objects")
def list_objects(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("catalog:read")),
    limit: int = Query(default=200, le=500),
    kind: str | None = None,
    parent_id: int | None = None,
    q: str | None = None,
    with_risks: bool = True,
) -> list[dict]:
    stmt = select(ObjectNode).order_by(ObjectNode.level, ObjectNode.id)
    if kind:
        stmt = stmt.where(ObjectNode.kind == kind)
    if parent_id is not None:
        stmt = stmt.where(ObjectNode.parent_id == parent_id)
    if q:
        stmt = stmt.where(ObjectNode.name.ilike(f"%{q}%"))
    rows = db.scalars(stmt.limit(limit)).all()
    risks = _risk_map(db, [row.id for row in rows]) if with_risks else {}
    result = []
    for row in rows:
        extra = risks.get(int(row.id), {})
        result.append(
            {
                "id": row.id,
                "level": row.level,
                "parent_id": row.parent_id,
                "kind": row.kind,
                "name": row.name,
                "open_risks": extra.get("open_risks", {}),
                "max_probability": extra.get("max_probability") or None,
            }
        )
    return result


def _ancestors(db: Session, node: ObjectNode) -> list[dict]:
    chain = []
    seen: set[int] = set()
    current = node
    while current.parent_id and current.parent_id not in seen:
        seen.add(int(current.parent_id))
        parent = db.get(ObjectNode, current.parent_id)
        if parent is None:
            break
        chain.append({"id": parent.id, "kind": parent.kind, "name": parent.name, "level": parent.level})
        current = parent
    chain.reverse()
    return chain


@router.get("/objects/{object_id}")
def get_object(
    object_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("catalog:read")),
) -> dict:
    node = db.get(ObjectNode, object_id)
    if node is None:
        raise HTTPException(status_code=404, detail="объект не найден")
    children = db.scalars(
        select(ObjectNode).where(ObjectNode.parent_id == object_id).order_by(ObjectNode.id)
    ).all()
    channels = db.scalars(select(Channel).where(Channel.object_id == object_id).order_by(Channel.id)).all()
    pickets: dict[str, list[dict]] = defaultdict(list)
    for channel in channels:
        key = channel.picket or "без пикета"
        pickets[key].append(
            {
                "id": channel.id,
                "sensor_type": channel.sensor_type,
                "system_type": channel.system_type,
                "name": channel.name,
                "tag": channel.tag,
            }
        )
    picket_rows = []
    for key, items in sorted(pickets.items(), key=lambda pair: _picket_sort(pair[0])):
        types = sorted({item["sensor_type"] for item in items})
        picket_rows.append(
            {
                "picket": key,
                "n_channels": len(items),
                "sensor_types": types[:8],
                "has_fire": any(t in {"Датчик дыма", "Тепловой датчик", "Газовый датчик", "Ручной извещатель"} for t in types),
                "has_flood": any(t in {"Датчик затопления", "Состояние насоса"} for t in types),
                "has_guard": any(t in {"Датчик движения", "КД АВ", "КД Дверь", "КД Люк", "Стекло"} for t in types),
            }
        )
    preds = db.scalars(
        select(Prediction)
        .where(Prediction.object_id == object_id, Prediction.status == "open")
        .order_by(Prediction.probability.desc())
    ).all()
    names = {int(node.id): node.name}
    return {
        "id": node.id,
        "level": node.level,
        "parent_id": node.parent_id,
        "kind": node.kind,
        "name": node.name,
        "ancestors": _ancestors(db, node),
        "children": [
            {"id": child.id, "kind": child.kind, "name": child.name, "level": child.level}
            for child in children
        ],
        "n_channels": len(channels),
        "n_pickets": len(picket_rows),
        "pickets": picket_rows,
        "predictions": [serialize_prediction(row, names) for row in preds],
    }


@router.get("/objects/{object_id}/channels")
def list_object_channels(
    object_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("catalog:read")),
) -> list[dict]:
    if db.get(ObjectNode, object_id) is None:
        raise HTTPException(status_code=404, detail="объект не найден")
    rows = db.scalars(select(Channel).where(Channel.object_id == object_id).order_by(Channel.id)).all()
    return [
        {
            "id": row.id,
            "sensor_type": row.sensor_type,
            "system_type": row.system_type,
            "name": row.name,
            "picket": row.picket,
            "tag": row.tag,
        }
        for row in rows
    ]


@router.get("/events")
def list_events(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("events:read")),
    object_id: int | None = None,
    channel_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sensor_type: str | None = None,
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    stmt = (
        select(EventAlarm, Channel, ObjectNode)
        .join(Channel, Channel.id == EventAlarm.channel_id, isouter=True)
        .join(ObjectNode, ObjectNode.id == Channel.object_id, isouter=True)
    )
    if object_id is not None:
        stmt = stmt.where(Channel.object_id == object_id)
    if channel_id is not None:
        stmt = stmt.where(EventAlarm.channel_id == channel_id)
    if date_from is not None:
        stmt = stmt.where(EventAlarm.event_ts >= date_from)
    if date_to is not None:
        stmt = stmt.where(EventAlarm.event_ts <= date_to)
    if sensor_type:
        stmt = stmt.where(Channel.sensor_type == sensor_type)
    stmt = stmt.order_by(EventAlarm.event_ts.desc()).limit(limit)
    rows = db.execute(stmt).all()
    return [
        {
            "event_id": alarm.event_id,
            "event_ts": alarm.event_ts.isoformat(),
            "channel_id": alarm.channel_id,
            "object_id": channel.object_id if channel else None,
            "object_name": obj.name if obj else None,
            "sensor_type": channel.sensor_type if channel else None,
            "picket": channel.picket if channel else None,
            "value_raw": alarm.value_raw,
            "value_cat": alarm.value_cat,
            "risk": classify_event(
                channel.sensor_type if channel else None,
                alarm.value_cat,
            ),
        }
        for alarm, channel, obj in rows
    ]


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("dashboard:read")),
) -> dict:
    open_rows = db.scalars(select(Prediction).where(Prediction.status == "open")).all()
    counts: dict[str, int] = defaultdict(int)
    objects: set[int] = set()
    for row in open_rows:
        counts[row.risk_type] += 1
        if row.object_id is not None:
            objects.add(int(row.object_id))
    names = object_names(db, {int(row.object_id) for row in open_rows if row.object_id is not None})
    top = sorted(open_rows, key=lambda item: item.probability, reverse=True)[:15]
    window = data_horizon(db)
    month = window[1].month if window else None
    return {
        "open_total": len(open_rows),
        "objects_at_risk": len(objects),
        "open_by_risk": {risk: counts.get(risk, 0) for risk in RISK_TITLES},
        "risk_titles": RISK_TITLES,
        "critical": [serialize_prediction(row, names) for row in top if row.probability >= 0.8],
        "top": [serialize_prediction(row, names) for row in top],
        "alarm_window": {
            "from": window[0].isoformat() if window else None,
            "to": window[1].isoformat() if window else None,
        },
        "season": {
            "month": month,
            "flood_season": month in {3, 4, 5, 6, 7, 8} if month else False,
            "heating_season": month in {11, 12, 1, 2} if month else False,
        },
        "stream_slo_seconds": 300,
    }
