from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.db import get_db
from app.domain import RISK_TITLES
from app.models import ObjectNode, Prediction, TicketDraft
from app.schemas import TicketIn
from app.services.audit import write_audit

router = APIRouter(tags=["tickets"])


def _out(row: TicketDraft) -> dict:
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "object_id": row.object_id,
        "title": row.title,
        "body": row.body,
        "status": row.status,
        "external_id": row.external_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/tickets")
def list_tickets(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("tickets:write")),
    status: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    stmt = select(TicketDraft).order_by(TicketDraft.id.desc())
    if status:
        stmt = stmt.where(TicketDraft.status == status)
    return [_out(row) for row in db.scalars(stmt.limit(limit)).all()]


@router.post("/tickets/draft")
def create_draft(
    body: TicketIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("tickets:write")),
) -> dict:
    object_id = body.object_id
    title = body.title
    text = body.body
    if body.prediction_id is not None:
        pred = db.get(Prediction, body.prediction_id)
        if pred is None:
            raise HTTPException(status_code=404, detail="прогноз не найден")
        object_id = object_id or pred.object_id
        title = title or f"{RISK_TITLES.get(pred.risk_type, pred.risk_type)}: объект {pred.object_id}"
        text = text or pred.recommendation or pred.explanation
    if object_id is not None and db.get(ObjectNode, object_id) is None:
        raise HTTPException(status_code=404, detail="объект не найден")
    if not title:
        raise HTTPException(status_code=400, detail="нужен title или prediction_id")
    row = TicketDraft(
        prediction_id=body.prediction_id,
        object_id=object_id,
        title=title,
        body=text,
        status="draft",
    )
    db.add(row)
    db.flush()
    write_audit(db, user.login, "ticket.draft", {"ticket_id": row.id, "prediction_id": body.prediction_id})
    db.commit()
    db.refresh(row)
    return _out(row)
