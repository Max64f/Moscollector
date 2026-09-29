"""Калибровка порогов LightGBM по журналу решений. Пишем в Postgres: /parquet/models смонтирован read-only."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain import RISKS
from app.models import DispatcherFeedback, ModelCalibration, Prediction

FALSE_STEP = 0.025
DISPATCH_STEP = 0.012
MAX_SHIFT = 0.15


def base_thresholds() -> dict[str, float]:
    path = Path(settings.models_dir) / "metrics.json"
    defaults = {risk: 0.5 for risk in RISKS}
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    stored = (raw.get("thresholds") or {}).get("lgbm") or {}
    out = dict(defaults)
    for risk in RISKS:
        if risk in stored:
            out[risk] = float(stored[risk])
    return out


def stored_calibration(db: Session) -> dict[str, ModelCalibration]:
    rows = db.scalars(select(ModelCalibration)).all()
    return {row.risk_type: row for row in rows}


def effective_thresholds(db: Session) -> dict[str, float]:
    base = base_thresholds()
    stored = stored_calibration(db)
    return {risk: stored[risk].threshold if risk in stored else base[risk] for risk in RISKS}


def calibrate_from_feedback(db: Session, user_login: str) -> list[dict]:
    base = base_thresholds()
    counts = db.execute(
        select(Prediction.risk_type, DispatcherFeedback.decision, func.count())
        .join(DispatcherFeedback, DispatcherFeedback.prediction_id == Prediction.id)
        .group_by(Prediction.risk_type, DispatcherFeedback.decision)
    ).all()
    by_risk: dict[str, dict[str, int]] = {risk: {"dispatch": 0, "false_alarm": 0, "maintenance": 0} for risk in RISKS}
    for risk, decision, n in counts:
        if risk in by_risk and decision in by_risk[risk]:
            by_risk[risk][decision] = int(n)
    result = []
    for risk in RISKS:
        n_false = by_risk[risk]["false_alarm"]
        n_dispatch = by_risk[risk]["dispatch"] + by_risk[risk]["maintenance"]
        shift = FALSE_STEP * n_false - DISPATCH_STEP * n_dispatch
        shift = max(-MAX_SHIFT, min(MAX_SHIFT, shift))
        threshold = round(min(0.95, max(0.05, base[risk] + shift)), 3)
        row = db.get(ModelCalibration, risk)
        if row is None:
            row = ModelCalibration(risk_type=risk, threshold=threshold)
            db.add(row)
        row.threshold = threshold
        row.n_dispatch = n_dispatch
        row.n_false_alarm = n_false
        row.user_login = user_login
        row.updated_at = datetime.now()
        result.append(
            {
                "risk": risk,
                "base_threshold": round(base[risk], 3),
                "threshold": threshold,
                "shift": round(shift, 3),
                "n_dispatch": n_dispatch,
                "n_false_alarm": n_false,
            }
        )
    db.flush()
    return result
