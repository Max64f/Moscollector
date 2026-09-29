"""Inference: последний час каждого объекта → Postgres.predictions."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg

from ml.baseline import persist_score, rule_score
from ml.config import (
    HORIZON_HOURS,
    MODELS_DIR,
    RECOMMENDATIONS,
    RISK_TITLES,
    RISKS,
    TEST_YEARS,
)
from ml.features import add_seasonal_features
from ml.train import _read_year, cpu_threads


def load_models() -> dict[str, lgb.Booster]:
    models = {}
    for risk in RISKS:
        path = MODELS_DIR / f"lgbm_{risk}.txt"
        if not path.exists():
            raise SystemExit(f"нет модели {path}, сначала python -m ml.train")
        models[risk] = lgb.Booster(model_file=str(path))
    return models


def load_metrics() -> dict:
    path = MODELS_DIR / "metrics.json"
    if not path.exists():
        raise SystemExit(f"нет {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def latest_rows(years: tuple[str, ...] = TEST_YEARS) -> pd.DataFrame:
    threads = cpu_threads()
    with ThreadPoolExecutor(max_workers=min(threads, len(years))) as pool:
        parts = list(pool.map(_read_year, years))
    frame = pd.concat(parts, ignore_index=True)
    latest = frame.sort_values("hour_ts").groupby("object_id", as_index=False).tail(1)
    return add_seasonal_features(latest.reset_index(drop=True))


def explain_row(row: pd.Series, risk: str, shap_features: list[dict]) -> str:
    bits = [f"{RISK_TITLES[risk]} на 24ч."]
    if risk == "fire" and row.get("fire_24h", 0):
        bits.append(f"пожарных сработок за сутки: {int(row['fire_24h'])}.")
    if risk == "flood" and row.get("flood_24h", 0):
        bits.append(f"водяных событий за сутки: {int(row['flood_24h'])}.")
    if risk == "flood" and pd.notna(row.get("precip")):
        bits.append(f"осадки: {row['precip']:.1f} мм.")
    if risk == "failure" and row.get("failure_new_24h", 0):
        bits.append(f"новых отказов за сутки: {int(row['failure_new_24h'])}.")
    if risk == "intrusion":
        bits.append("на охране." if row.get("guard_on", 0) else "снято с охраны.")
        if row.get("intrusion_24h", 0):
            bits.append(f"охранных тревог за сутки: {int(row['intrusion_24h'])}.")
    if risk == "flood" and row.get("flood_season"):
        bits.append("сезон паводков/ливней.")
    if risk == "failure" and row.get("heating_season"):
        bits.append("отопительный сезон.")
    top = ", ".join(item["feature"] for item in shap_features[:3])
    if top:
        bits.append(f"ключевые признаки: {top}.")
    return " ".join(bits)


def score(frame: pd.DataFrame, models: dict[str, lgb.Booster]) -> dict[str, np.ndarray]:
    n_workers = min(len(RISKS), cpu_threads())

    def _pred(risk: str) -> tuple[str, np.ndarray]:
        names = models[risk].feature_name()
        use = [col for col in names if col in frame.columns]
        missing = [col for col in names if col not in frame.columns]
        if missing:
            raise SystemExit(f"{risk}: в кадре нет колонок {missing}")
        x = frame[use].astype(np.float32, copy=False)
        return risk, models[risk].predict(x)

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        return dict(pool.map(_pred, RISKS))


def overlay_thresholds(metrics: dict, dsn: str | None = None) -> dict[str, float]:
    thresholds = {str(k): float(v) for k, v in (metrics.get("thresholds") or {}).get("lgbm", {}).items()}
    url = dsn or os.environ.get("DATABASE_URL", "postgresql://ldt:ldt@db:5432/moscollector")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(url) as conn:
            calib = conn.execute("SELECT risk_type, threshold FROM model_calibration").fetchall()
        for risk, thr in calib:
            thresholds[str(risk)] = float(thr)
    except Exception:
        pass
    return thresholds


def write_postgres(frame: pd.DataFrame, probs: dict[str, np.ndarray], metrics: dict) -> int:
    dsn = os.environ.get("DATABASE_URL", "postgresql://ldt:ldt@db:5432/moscollector")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    thresholds = overlay_thresholds(metrics, dsn)
    shap_map = metrics.get("shap") or {}
    rows = []
    for i, row in enumerate(frame.itertuples(index=False)):
        object_id = int(row.object_id)
        series = frame.iloc[i]
        for risk in RISKS:
            proba = float(probs[risk][i])
            if proba < thresholds[risk]:
                continue
            rows.append(
                (
                    object_id,
                    risk,
                    proba,
                    HORIZON_HOURS,
                    explain_row(series, risk, shap_map.get(risk, [])),
                    RECOMMENDATIONS[risk],
                    "open",
                )
            )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM predictions WHERE status = 'open'")
        if rows:
            cur.executemany(
                """
                INSERT INTO predictions
                    (object_id, risk_type, probability, horizon_hours, explanation, recommendation, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--also-rules", action="store_true", help="печать rule-скоров в лог")
    args = parser.parse_args()
    models = load_models()
    metrics = load_metrics()
    frame = latest_rows()
    print(f"объектов к скорингу: {len(frame)}, час={frame['hour_ts'].max()}", flush=True)
    probs = score(frame, models)
    thresholds = overlay_thresholds(metrics)
    for risk in RISKS:
        n = int((probs[risk] >= thresholds[risk]).sum())
        print(f"  {risk:10} выше порога {thresholds[risk]:.3f}: {n}", flush=True)
        if args.also_rules:
            r = rule_score(frame, risk).sum()
            p = persist_score(frame, risk)
            print(f"    rules={int(r)} persist_mean={p.mean():.3f}", flush=True)
    if args.skip_db:
        return
    n = write_postgres(frame, probs, metrics)
    print(f"записано прогнозов: {n}", flush=True)


if __name__ == "__main__":
    main()
