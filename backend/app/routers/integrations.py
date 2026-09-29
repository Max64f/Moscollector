from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.config import parse_category, parse_number
from app.db import get_db
from app.models import Channel, EventAlarm, TicketDraft
from app.schemas import SmvuBatchIn
from app.services.audit import write_audit
from app.services.scoring import upsert_rule_predictions

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("/smvu/events")
def smvu_events(
    body: SmvuBatchIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("integrations:write")),
) -> dict:
    """Заглушка СМВУ: принимаем пачку событий, тревоги кладём в витрину."""
    inserted = 0
    skipped = 0
    objects: set[int] = set()
    next_id = db.scalar(select(func.coalesce(func.max(EventAlarm.event_id), 0))) or 0
    for item in body.events:
        if not item.alarm:
            skipped += 1
            continue
        channel = db.get(Channel, item.channel_id)
        if channel is None:
            raise HTTPException(status_code=400, detail=f"нет канала {item.channel_id}")
        next_id += 1
        event_id = item.event_id or next_id
        if db.get(EventAlarm, event_id) is not None:
            skipped += 1
            continue
        number = parse_number(item.value)
        db.add(
            EventAlarm(
                event_id=event_id,
                channel_id=item.channel_id,
                event_ts=item.event_ts or datetime.now(timezone.utc).replace(tzinfo=None),
                value_raw=item.value,
                value_num=number,
                value_cat=parse_category(item.value, number),
            )
        )
        inserted += 1
        if channel.object_id is not None:
            objects.add(int(channel.object_id))
    created = 0
    for oid in objects:
        created += len(upsert_rule_predictions(db, object_id=oid))
    write_audit(
        db,
        user.login,
        "integrations.smvu",
        {"inserted": inserted, "skipped": skipped, "rescored_objects": list(objects)},
    )
    db.commit()
    return {
        "stub": True,
        "system": "SMVU",
        "inserted": inserted,
        "skipped": skipped,
        "rules_written": created,
        "detail": "Боевой контур СМВУ не подключён. События пишутся в events_alarm как в импорте CSV.",
    }


@router.get("/ods/journals")
def ods_journals(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("events:read")),
    limit: int = 20,
) -> dict:
    """Заглушка выгрузки журнала ОДС: последние тревоги витрины."""
    stmt = (
        select(EventAlarm, Channel)
        .join(Channel, Channel.id == EventAlarm.channel_id, isouter=True)
        .order_by(EventAlarm.event_ts.desc())
        .limit(limit)
    )
    rows = []
    for alarm, channel in db.execute(stmt):
        rows.append(
            {
                "event_id": alarm.event_id,
                "ts": alarm.event_ts.isoformat(),
                "object_id": channel.object_id if channel else None,
                "channel": channel.name if channel else None,
                "sensor_type": channel.sensor_type if channel else None,
                "value": alarm.value_cat or alarm.value_raw,
            }
        )
    return {
        "stub": True,
        "system": "ODS",
        "detail": "Вместо LDAP/журналов ОДС заказчика отдаём витрину events_alarm.",
        "items": rows,
    }


@router.post("/tickets")
def send_ticket(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("integrations:write")),
    draft_id: int | None = None,
) -> dict:
    """Заглушка внешней заявки (ППР / Service Desk)."""
    row = None
    if draft_id is not None:
        row = db.get(TicketDraft, draft_id)
        if row is None:
            raise HTTPException(status_code=404, detail="черновик не найден")
    else:
        row = db.scalar(select(TicketDraft).where(TicketDraft.status == "draft").order_by(TicketDraft.id.desc()))
        if row is None:
            raise HTTPException(status_code=404, detail="нет черновиков")
    row.status = "sent"
    row.external_id = f"ODS-{row.id:06d}"
    write_audit(db, user.login, "integrations.ticket", {"draft_id": row.id, "external_id": row.external_id})
    db.commit()
    return {
        "stub": True,
        "system": "tickets",
        "draft_id": row.id,
        "external_id": row.external_id,
        "status": row.status,
        "detail": "Реестр заявок заказчика не подключён. Идентификатор синтетический.",
    }
