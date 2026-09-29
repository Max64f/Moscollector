from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import parse_alarm, parse_category, parse_number, parse_timestamp
from app.models import Channel, EventAlarm, ObjectNode

PICKET_RE = re.compile(r"ПК\s*(\d+(?:[.,+]\d+)*)", re.IGNORECASE)

OBJECT_FIELDS = {"ид_объект", "иерархия_уровень", "родитель", "вид_объекта", "диспетчерское_название_объекта"}
CHANNEL_FIELDS = {"ид_канала_данных", "тип_инж_системы", "тип_датчика", "ид_объект"}
EVENT_FIELDS = {"ид_события", "ид_канала_данных", "дата", "тревожное"}


def detect_kind(header: list[str]) -> str:
    fields = {item.strip().strip('"') for item in header}
    if OBJECT_FIELDS <= fields:
        return "objects"
    if CHANNEL_FIELDS <= fields:
        return "channels"
    if EVENT_FIELDS <= fields:
        return "journal"
    raise ValueError(f"Неизвестный формат CSV, колонки: {sorted(fields)}")


def _open_text(source: Path | str | bytes) -> io.StringIO | io.TextIOWrapper:
    if isinstance(source, bytes):
        return io.StringIO(source.decode("utf-8-sig"))
    if isinstance(source, str) and not Path(source).exists():
        return io.StringIO(source)
    path = Path(source)
    return path.open("r", encoding="utf-8-sig", newline="")


def parse_picket(name: str | None) -> str | None:
    if not name:
        return None
    match = PICKET_RE.search(name)
    return match.group(1) if match else None


def _rows(source: Path | str | bytes) -> tuple[str, list[dict[str, str]]]:
    handle = _open_text(source)
    try:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV без заголовка")
        header = [name.strip().strip('"') for name in reader.fieldnames]
        kind = detect_kind(header)
        rows = []
        for raw in reader:
            rows.append({(k or "").strip().strip('"'): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()})
        return kind, rows
    finally:
        handle.close()


def import_objects(db: Session, rows: list[dict[str, str]]) -> int:
    payload = []
    for row in rows:
        parent_raw = row.get("родитель") or None
        parent_id = int(parent_raw) if parent_raw else None
        payload.append(
            {
                "id": int(row["ид_объект"]),
                "level": int(row["иерархия_уровень"]),
                "parent_id": parent_id,
                "kind": row["вид_объекта"],
                "name": row["диспетчерское_название_объекта"],
            }
        )
    ids = {item["id"] for item in payload}
    for item in payload:
        if item["parent_id"] not in ids:
            item["parent_id"] = None
    payload.sort(key=lambda item: item["level"])
    for item in payload:
        db.merge(ObjectNode(**item))
    db.commit()
    return len(payload)


def import_channels(db: Session, rows: list[dict[str, str]]) -> int:
    known_objects = set(db.scalars(select(ObjectNode.id)).all())
    count = 0
    for row in rows:
        object_id = int(row["ид_объект"]) if row.get("ид_объект") else None
        if object_id not in known_objects:
            object_id = None
        name = row.get("название_датчика") or ""
        db.merge(
            Channel(
                id=int(row["ид_канала_данных"]),
                system_type=row.get("тип_инж_системы") or "",
                sensor_type=row.get("тип_датчика") or "",
                tag=row.get("тег_инженерной_системы"),
                name=name,
                object_id=object_id,
                picket=parse_picket(name),
            )
        )
        count += 1
    db.commit()
    return count


def import_journal_alarms(db: Session, rows: list[dict[str, str]]) -> int:
    inserted = 0
    batch: list[dict] = []
    for row in rows:
        if not parse_alarm(row.get("тревожное")):
            continue
        number = parse_number(row.get("значение_датчика"))
        ts = parse_timestamp(row.get("дата"), row.get("время"))
        if ts is None:
            continue
        batch.append(
            {
                "event_id": int(row["ид_события"]),
                "channel_id": int(row["ид_канала_данных"]),
                "event_ts": ts,
                "value_raw": row.get("значение_датчика"),
                "value_num": number,
                "value_cat": parse_category(row.get("значение_датчика"), number),
            }
        )
        if len(batch) >= 1000:
            inserted += _upsert_alarms(db, batch)
            batch = []
    if batch:
        inserted += _upsert_alarms(db, batch)
    db.commit()
    return inserted


def _upsert_alarms(db: Session, batch: list[dict]) -> int:
    stmt = insert(EventAlarm).values(batch)
    stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
    db.execute(stmt)
    return len(batch)


def import_csv(db: Session, source: Path | str | bytes, kind: str | None = None) -> dict:
    detected, rows = _rows(source)
    resolved = kind or detected
    if resolved == "objects":
        count = import_objects(db, rows)
    elif resolved == "channels":
        count = import_channels(db, rows)
    elif resolved == "journal":
        count = import_journal_alarms(db, rows)
    else:
        raise ValueError(f"Неизвестный kind={resolved}")
    return {"kind": resolved, "rows_read": len(rows), "upserted": count}
