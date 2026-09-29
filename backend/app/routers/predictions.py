from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.db import get_db
from app.domain import DECISIONS, DECISION_TITLES, RISK_TITLES
from app.models import DispatcherFeedback, ObjectNode, Prediction, TicketDraft
from app.schemas import DecisionIn
from app.services.audit import write_audit
from app.services.scoring import object_names, serialize_prediction, upsert_rule_predictions

router = APIRouter(tags=["predictions"])


@router.get("/predictions")
def list_predictions(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("predictions:read")),
    status: str = "open",
    risk_type: str | None = None,
    object_id: int | None = None,
    source: str | None = None,
    limit: int = Query(default=100, le=500),
) -> list[dict]:
    stmt = select(Prediction).order_by(Prediction.probability.desc(), Prediction.id.desc())
    if status != "all":
        stmt = stmt.where(Prediction.status == status)
    if risk_type:
        stmt = stmt.where(Prediction.risk_type == risk_type)
    if object_id is not None:
        stmt = stmt.where(Prediction.object_id == object_id)
    if source:
        stmt = stmt.where(Prediction.source == source)
    rows = db.scalars(stmt.limit(limit)).all()
    names = object_names(db, {int(row.object_id) for row in rows if row.object_id is not None})
    return [serialize_prediction(row, names) for row in rows]


@router.get("/predictions/{prediction_id}")
def get_prediction(
    prediction_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("predictions:read")),
) -> dict:
    row = db.get(Prediction, prediction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="прогноз не найден")
    names = object_names(db, {int(row.object_id)} if row.object_id else set())
    feedback = db.scalars(
        select(DispatcherFeedback)
        .where(DispatcherFeedback.prediction_id == prediction_id)
        .order_by(DispatcherFeedback.id.desc())
    ).all()
    payload = serialize_prediction(row, names)
    payload["feedback"] = [
        {
            "id": item.id,
            "decision": item.decision,
            "decision_title": DECISION_TITLES.get(item.decision, item.decision),
            "reason": item.reason,
            "user_login": item.user_login,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in feedback
    ]
    return payload


@router.patch("/predictions/{prediction_id}/decision")
def decide(
    prediction_id: int,
    body: DecisionIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("predictions:decide")),
) -> dict:
    if body.decision not in DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision={','.join(DECISIONS)}")
    row = db.get(Prediction, prediction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="прогноз не найден")
    row.status = body.decision
    db.add(
        DispatcherFeedback(
            prediction_id=row.id,
            decision=body.decision,
            reason=body.reason,
            user_login=user.login,
        )
    )
    ticket = None
    if body.create_ticket:
        title = f"{RISK_TITLES.get(row.risk_type, row.risk_type)}: объект {row.object_id}"
        ticket = TicketDraft(
            prediction_id=row.id,
            object_id=row.object_id,
            title=title,
            body=body.reason or row.recommendation or row.explanation,
            status="draft",
        )
        db.add(ticket)
        db.flush()
    write_audit(
        db,
        user.login,
        "prediction.decision",
        {
            "prediction_id": row.id,
            "decision": body.decision,
            "object_id": row.object_id,
            "ticket_id": ticket.id if ticket else None,
        },
    )
    db.commit()
    db.refresh(row)
    names = object_names(db, {int(row.object_id)} if row.object_id else set())
    result = serialize_prediction(row, names)
    result["decision"] = body.decision
    result["ticket_id"] = ticket.id if ticket else None
    return result


@router.post("/predict/object/{object_id}")
def predict_object(
    object_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("predictions:decide")),
) -> dict:
    if db.get(ObjectNode, object_id) is None:
        raise HTTPException(status_code=404, detail="объект не найден")
    created = upsert_rule_predictions(db, object_id=object_id)
    write_audit(db, user.login, "predict.object", {"object_id": object_id, "created": len(created)})
    db.commit()
    open_rows = db.scalars(
        select(Prediction)
        .where(Prediction.object_id == object_id, Prediction.status == "open")
        .order_by(Prediction.probability.desc())
    ).all()
    names = object_names(db, {object_id})
    return {
        "object_id": object_id,
        "rules_written": len(created),
        "open": [serialize_prediction(row, names) for row in open_rows],
        "note": "Правила по журналу тревог за 24ч витрины. Полный LightGBM: python -m ml.predict",
    }


@router.post("/predict/run")
def predict_run(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("predictions:decide")),
    object_id: int | None = None,
) -> dict:
    if object_id is not None and db.get(ObjectNode, object_id) is None:
        raise HTTPException(status_code=404, detail="объект не найден")
    created = upsert_rule_predictions(db, object_id=object_id)
    write_audit(db, user.login, "predict.run", {"object_id": object_id, "created": len(created)})
    db.commit()
    return {
        "rules_written": len(created),
        "object_id": object_id,
        "note": "Открытые LightGBM-прогнозы не трогаем. Полный пересчёт LGBM: docker compose --profile ml run --rm ml python -m ml.predict",
    }
