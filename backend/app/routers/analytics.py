import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require
from app.config import settings
from app.db import get_db
from app.domain import DECISION_TITLES, RISK_TITLES
from app.models import AuditLog, DispatcherFeedback, Prediction
from app.services.audit import write_audit
from app.services.calibration import (
    calibrate_from_feedback,
    effective_thresholds,
    stored_calibration,
)

router = APIRouter(tags=["analytics"])


def _metrics_payload() -> dict | None:
    path = Path(settings.models_dir) / "metrics.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/analytics/metrics")
def analytics_metrics(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("analytics:read")),
) -> dict:
    raw = _metrics_payload()
    if raw is None:
        return {
            "available": False,
            "detail": "нет models/metrics.json — сначала python -m ml.train",
        }
    test = raw.get("test", {}).get("lgbm", {})
    table = []
    calib = effective_thresholds(db)
    stored = stored_calibration(db)
    for risk, title in RISK_TITLES.items():
        row = test.get(risk) or {}
        cal = stored.get(risk)
        table.append(
            {
                "risk": risk,
                "title": title,
                "precision": row.get("precision"),
                "recall": row.get("recall"),
                "pr_auc": row.get("pr_auc"),
                "threshold": row.get("threshold"),
                "calibrated_threshold": calib.get(risk),
                "n_dispatch": cal.n_dispatch if cal else 0,
                "n_false_alarm": cal.n_false_alarm if cal else 0,
            }
        )
    return {
        "available": True,
        "trained_at": raw.get("trained_at"),
        "retrained_at": raw.get("retrained_at"),
        "split": {"train": raw.get("n_train"), "valid": raw.get("n_valid"), "test": raw.get("n_test")},
        "test_lgbm": table,
        "calibration": [
            {
                "risk": risk,
                "title": RISK_TITLES[risk],
                "threshold": calib[risk],
                "n_dispatch": stored[risk].n_dispatch if risk in stored else 0,
                "n_false_alarm": stored[risk].n_false_alarm if risk in stored else 0,
                "updated_at": stored[risk].updated_at.isoformat() if risk in stored and stored[risk].updated_at else None,
            }
            for risk in RISK_TITLES
        ],
        "note": "Метки эвристические, это не подтверждённые инциденты. Пороги калибруются в Postgres по решениям диспетчера. См. docs/models.md",
    }


@router.get("/analytics/feedback")
def analytics_feedback(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("analytics:read")),
) -> dict:
    pred_counts = db.execute(
        select(Prediction.risk_type, Prediction.status, func.count()).group_by(
            Prediction.risk_type, Prediction.status
        )
    ).all()
    fb = db.execute(
        select(DispatcherFeedback.decision, func.count()).group_by(DispatcherFeedback.decision)
    ).all()
    by_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for risk, status, n in pred_counts:
        by_status[risk][status] = int(n)
    return {
        "predictions_by_risk_status": {risk: dict(statuses) for risk, statuses in by_status.items()},
        "decisions": [
            {"decision": key, "title": DECISION_TITLES.get(key, key), "n": int(n)}
            for key, n in fb
        ],
        "open": db.scalar(select(func.count()).select_from(Prediction).where(Prediction.status == "open")) or 0,
        "closed": db.scalar(select(func.count()).select_from(Prediction).where(Prediction.status != "open")) or 0,
    }


@router.post("/analytics/retrain")
def analytics_retrain(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require("analytics:write")),
) -> dict:
    """Сдвигает пороги LightGBM по журналу решений. Деревья не трогает: /parquet/models read-only."""
    rows = calibrate_from_feedback(db, user.login)
    write_audit(db, user.login, "analytics.calibrate", {"rows": rows})
    db.commit()
    return {
        "mode": "threshold_calibration",
        "applied": rows,
        "detail": (
            "Пороги записаны в model_calibration. Ложные тревоги поднимают порог, выезды слегка опускают. "
            "Полное дообучение деревьев: docker compose --profile ml run --rm ml python -m ml.retrain"
        ),
    }


@router.get("/audit")
def list_audit(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require("audit:read")),
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "user_login": row.user_login,
            "action": row.action,
            "details": row.details,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
