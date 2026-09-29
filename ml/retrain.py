"""Дообучение LightGBM на решениях диспетчера. Продолжает деревья, не учит с нуля."""

from __future__ import annotations

import json
import os
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg

from ml.config import MODELS_DIR, RISKS, TEST_YEARS
from ml.features import add_seasonal_features
from ml.train import _read_year, cpu_threads

LABEL_FROM_DECISION = {
    "dispatch": 1.0,
    "maintenance": 1.0,
    "false_alarm": 0.0,
}


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "postgresql://ldt:ldt@db:5432/moscollector")
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def load_feedback() -> pd.DataFrame:
    sql = """
        SELECT p.object_id, p.risk_type, f.decision, f.created_at
        FROM dispatcher_feedback f
        JOIN predictions p ON p.id = f.prediction_id
        WHERE p.object_id IS NOT NULL
    """
    with psycopg.connect(_dsn()) as conn:
        rows = conn.execute(sql).fetchall()
    if not rows:
        return pd.DataFrame(columns=["object_id", "risk_type", "decision", "y"])
    frame = pd.DataFrame(rows, columns=["object_id", "risk_type", "decision", "created_at"])
    frame["y"] = frame["decision"].map(LABEL_FROM_DECISION)
    return frame.dropna(subset=["y"])


def latest_features() -> pd.DataFrame:
    parts = [_read_year(year) for year in TEST_YEARS]
    frame = add_seasonal_features(pd.concat(parts, ignore_index=True))
    return frame.sort_values("hour_ts").groupby("object_id", as_index=False).tail(1)


def main() -> None:
    feedback = load_feedback()
    if feedback.empty:
        raise SystemExit("нет решений диспетчера (dispatch / false_alarm / maintenance)")
    print(f"feedback rows={len(feedback)}", flush=True)
    latest = latest_features()
    threads = max(2, cpu_threads() // max(len(RISKS), 1))
    report = {}
    for risk in RISKS:
        subset = feedback[feedback["risk_type"] == risk]
        if subset.empty:
            print(f"  {risk}: нет feedback, пропуск", flush=True)
            continue
        merged = subset.merge(latest, on="object_id", how="inner")
        if merged.empty:
            print(f"  {risk}: объекты feedback нет в features, пропуск", flush=True)
            continue
        model_path = MODELS_DIR / f"lgbm_{risk}.txt"
        if not model_path.exists():
            raise SystemExit(f"нет {model_path}")
        booster = lgb.Booster(model_file=str(model_path))
        names = booster.feature_name()
        use = [col for col in names if col in merged.columns]
        x = merged[use].astype(np.float32)
        y = merged["y"].to_numpy(dtype=np.float32)
        weight = np.where(y == 0, 3.0, 2.0)
        extra = lgb.Dataset(x, label=y, weight=weight, feature_name=use, free_raw_data=False)
        params = {
            "objective": "binary",
            "metric": "average_precision",
            "learning_rate": 0.02,
            "verbosity": -1,
            "num_threads": threads,
        }
        backup = MODELS_DIR / f"lgbm_{risk}.prev.txt"
        booster.save_model(str(backup))
        updated = lgb.train(
            params,
            extra,
            num_boost_round=40,
            init_model=booster,
        )
        updated.save_model(str(model_path))
        report[risk] = {"n": int(len(merged)), "trees": int(updated.num_trees())}
        print(f"  {risk}: +{len(merged)} примеров, trees={updated.num_trees()}", flush=True)

    metrics_path = MODELS_DIR / "metrics.json"
    payload = {}
    if metrics_path.exists():
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    payload["retrained_at"] = datetime.now().isoformat(timespec="seconds")
    payload["retrain"] = report
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"metrics: {metrics_path}", flush=True)


if __name__ == "__main__":
    main()
