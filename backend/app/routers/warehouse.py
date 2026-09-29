from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.config import CHANNELS_CSV, EXAMPLE_CSV, OBJECTS_CSV, settings
from app.db import get_db
from app.models import Channel, EventAlarm, EventNumericHourly, ObjectNode
from app.services.audit import write_audit
from app.services.csv_import import import_csv

router = APIRouter(tags=["warehouse"])


@router.post("/import/csv")
def import_csv_from_data_dir(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("import:write")),
    kind: str = Query(..., description="objects | channels | journal | example"),
) -> dict:
    mapping = {
        "objects": (settings.data_dir / OBJECTS_CSV, "objects"),
        "channels": (settings.data_dir / CHANNELS_CSV, "channels"),
        "journal": (settings.data_dir / EXAMPLE_CSV, "journal"),
        "example": (settings.data_dir / EXAMPLE_CSV, "journal"),
    }
    if kind not in mapping:
        raise HTTPException(status_code=400, detail="kind=objects|channels|journal")
    path, load_kind = mapping[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Файл не найден: {path}")
    try:
        result = import_csv(db, path, kind=load_kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, user.login, "import.csv", {"kind": kind})
    db.commit()
    return result


@router.post("/import/upload")
async def import_csv_upload(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("import:write")),
    file: UploadFile = File(...),
    kind: str | None = Query(default=None),
) -> dict:
    payload = await file.read()
    try:
        result = import_csv(db, payload, kind=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["filename"] = file.filename
    write_audit(db, user.login, "import.upload", {"filename": file.filename})
    db.commit()
    return result


@router.post("/import/bootstrap")
def import_bootstrap(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("import:write")),
) -> dict:
    results = []
    for kind in ("objects", "channels", "journal"):
        mapping = {
            "objects": settings.data_dir / OBJECTS_CSV,
            "channels": settings.data_dir / CHANNELS_CSV,
            "journal": settings.data_dir / EXAMPLE_CSV,
        }
        results.append(import_csv(db, mapping[kind], kind=kind))
    write_audit(db, user.login, "import.bootstrap", {"steps": len(results)})
    db.commit()
    return {"results": results}


@router.get("/warehouse/stats")
def warehouse_stats(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("catalog:read")),
) -> dict:
    return {
        "objects": db.scalar(select(func.count()).select_from(ObjectNode)) or 0,
        "channels": db.scalar(select(func.count()).select_from(Channel)) or 0,
        "alarms": db.scalar(select(func.count()).select_from(EventAlarm)) or 0,
        "hourly": db.scalar(select(func.count()).select_from(EventNumericHourly)) or 0,
    }
