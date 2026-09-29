"""Обучение LightGBM по четырём рискам. Потоки: чтение Parquet + OpenMP внутри деревьев."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.baseline import persist_score, rule_score
from ml.config import (
    DOCS_DIR,
    FEATURES_DIR,
    ID_COLUMNS,
    LABEL_COLUMNS,
    MODELS_DIR,
    RECOMMENDATIONS,
    RISK_TITLES,
    RISKS,
    TARGET_METRICS,
    TEST_YEARS,
    TRAIN_YEARS,
    VALID_YEARS,
)
from ml.features import add_seasonal_features

RNG = 42


def cpu_threads() -> int:
    env = os.environ.get("LGBM_NUM_THREADS") or os.environ.get("OMP_NUM_THREADS")
    if env:
        return max(1, int(env))
    return max(1, os.cpu_count() or 4)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    skip = set(ID_COLUMNS) | set(LABEL_COLUMNS)
    return [col for col in frame.columns if col not in skip]


def _read_year(year: str) -> pd.DataFrame:
    path = FEATURES_DIR / f"features_{year}.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(path, engine="pyarrow")


def load_split(years: tuple[str, ...], n_readers: int) -> pd.DataFrame:
    if len(years) == 1:
        return _read_year(years[0])
    with ThreadPoolExecutor(max_workers=min(n_readers, len(years))) as pool:
        parts = list(pool.map(_read_year, years))
    return pd.concat(parts, ignore_index=True)


def _fit_one(
    risk: str,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_valid: pd.DataFrame,
    y_valid: np.ndarray,
    threads: int,
) -> lgb.Booster:
    n_pos = max(int(y_train.sum()), 1)
    n_neg = max(int(len(y_train) - n_pos), 1)
    params = {
        "objective": "binary",
        "metric": "average_precision",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 200,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": RNG,
        "num_threads": threads,
        "scale_pos_weight": n_neg / n_pos,
        "force_row_wise": True,
    }
    train_set = lgb.Dataset(x_train, label=y_train, free_raw_data=False)
    valid_set = lgb.Dataset(x_valid, label=y_valid, reference=train_set, free_raw_data=False)
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=500,
        valid_sets=[valid_set],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(40, verbose=False),
            lgb.log_evaluation(0),
        ],
    )
    return booster


def shap_top(booster: lgb.Booster, sample: pd.DataFrame, risk: str, threads: int) -> list[dict]:
    try:
        import shap

        explainer = shap.TreeExplainer(booster)
        values = explainer.shap_values(sample)
        if isinstance(values, list):
            values = values[1]
        mean_abs = np.abs(values).mean(axis=0)
        order = np.argsort(mean_abs)[::-1][:12]
        return [
            {"feature": sample.columns[i], "mean_abs_shap": float(mean_abs[i])}
            for i in order
        ]
    except Exception as exc:  # noqa: BLE001
        print(f"  SHAP {risk}: fallback на gain ({exc})", flush=True)
        gain = booster.feature_importance(importance_type="gain")
        names = booster.feature_name()
        order = np.argsort(gain)[::-1][:12]
        return [{"feature": names[i], "gain": float(gain[i])} for i in order]


def evaluate_all(
    name: str,
    frame: pd.DataFrame,
    scores: dict[str, np.ndarray],
    thresholds: dict[str, float],
) -> dict[str, dict]:
    report = {}
    for risk in RISKS:
        y = frame[f"y_{risk}"].to_numpy(dtype=np.int32)
        report[risk] = metrics_at_threshold(y, scores[risk], thresholds[risk])
        print(
            f"  {name:5} {risk:10} P={report[risk]['precision']:.3f} "
            f"R={report[risk]['recall']:.3f} PR-AUC={report[risk]['pr_auc']:.3f} "
            f"thr={report[risk]['threshold']:.3f}",
            flush=True,
        )
    return report


def write_docs(payload: dict) -> None:
    lines = [
        "# Модели прогноза (фаза 4)",
        "",
        f"Обучено {payload['trained_at']}. Потоки: {payload['threads']} "
        f"(OpenMP LightGBM + параллельное чтение Parquet и обучение 4 рисков).",
        "",
        "Сплит по времени: train 2019–2024, valid 2025, test 2026.",
        "Целевые метрики — из [data_profile.md](data_profile.md). "
        "LightGBM должен бить rule baseline по PR-AUC.",
        "",
        "## Test 2026",
        "",
        "| Риск | Метод | Precision | Recall | PR-AUC | Порог | Цель |",
        "|---|---|---|---|---|---|---|",
    ]
    for risk in RISKS:
        tgt = TARGET_METRICS[risk]
        goal = f"P≥{tgt['min_precision']:.2f}, R≥{tgt['min_recall']:.2f}"
        for method in ("persist", "rules", "lgbm"):
            row = payload["test"][method][risk]
            marker = ""
            if method == "lgbm":
                ok_p = row["precision"] >= tgt["min_precision"]
                ok_r = row["recall"] >= tgt["min_recall"]
                marker = "да" if ok_p and ok_r else "нет"
            lines.append(
                f"| {RISK_TITLES[risk]} | {method} | {row['precision']:.3f} | "
                f"{row['recall']:.3f} | {row['pr_auc']:.3f} | {row['threshold']:.3f} | {marker or goal} |"
            )
    lines += [
        "",
        "## Valid 2025 (порог выбран здесь)",
        "",
        "| Риск | LightGBM P | R | PR-AUC |",
        "|---|---|---|---|",
    ]
    for risk in RISKS:
        row = payload["valid"]["lgbm"][risk]
        lines.append(
            f"| {RISK_TITLES[risk]} | {row['precision']:.3f} | {row['recall']:.3f} | {row['pr_auc']:.3f} |"
        )
    lines += ["", "## Важность признаков (SHAP, valid sample)", ""]
    for risk in RISKS:
        lines.append(f"### {RISK_TITLES[risk]}")
        lines.append("")
        for item in payload["shap"].get(risk, [])[:8]:
            key = "mean_abs_shap" if "mean_abs_shap" in item else "gain"
            lines.append(f"- `{item['feature']}`: {item[key]:.4f}")
        lines.append("")
    lines += [
        "## Как читать метрики",
        "",
        "Baseline `persist` — «если такие события уже шли сутки, они продолжатся». "
        "Это сильный соперник из-за персистентности (см. [features.md](features.md)). "
        "Правила добавляют соседей по пикетам, осадки и режим охраны. "
        "LightGBM обязан обходить оба по PR-AUC на тесте.",
        "",
        "Порог для пожара и подтопления выбирается как максимальная точность "
        "при полноте не ниже цели. Для НСД — наоборот: сначала точность ≥ 0.80.",
        "",
        "## Артефакты",
        "",
        f"- модели: `{payload['models_dir']}/lgbm_<risk>.txt`",
        f"- пороги и метрики: `{payload['models_dir']}/metrics.json`",
        "",
        "Inference: `python -m ml.predict` записывает открытые прогнозы в Postgres.",
    ]
    dest = DOCS_DIR / "models.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"docs: {dest}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shap-sample", type=int, default=8000)
    args = parser.parse_args()
    threads = cpu_threads()
    print(f"CPU threads={threads}, risks={list(RISKS)}", flush=True)

    print("читаю train/valid/test...", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_train = pool.submit(load_split, TRAIN_YEARS, threads)
        fut_valid = pool.submit(load_split, VALID_YEARS, 1)
        fut_test = pool.submit(load_split, TEST_YEARS, 1)
        train = fut_train.result()
        valid = fut_valid.result()
        test = fut_test.result()
    train = add_seasonal_features(train)
    valid = add_seasonal_features(valid)
    test = add_seasonal_features(test)
    print(f"train={len(train):,} valid={len(valid):,} test={len(test):,}", flush=True)

    feats = feature_columns(train)
    x_train = train[feats].astype(np.float32, copy=False)
    x_valid = valid[feats].astype(np.float32, copy=False)
    x_test = test[feats].astype(np.float32, copy=False)

    persist_valid = {risk: persist_score(valid, risk) for risk in RISKS}
    persist_test = {risk: persist_score(test, risk) for risk in RISKS}
    rules_valid = {risk: rule_score(valid, risk) for risk in RISKS}
    rules_test = {risk: rule_score(test, risk) for risk in RISKS}

    persist_thr = {}
    rules_thr = {}
    for risk in RISKS:
        tgt = TARGET_METRICS[risk]
        yv = valid[f"y_{risk}"].to_numpy(dtype=np.int32)
        persist_thr[risk] = choose_threshold(
            yv, persist_valid[risk], min_recall=tgt["min_recall"],
            min_precision=tgt["min_precision"], strategy=tgt["strategy"],
        )
        rules_thr[risk] = 0.5

    threads_each = max(2, threads // len(RISKS))
    print(f"обучаю {len(RISKS)} модели параллельно, {threads_each} потоков на модель", flush=True)
    boosters: dict[str, lgb.Booster] = {}
    with ThreadPoolExecutor(max_workers=len(RISKS)) as pool:
        futs = {
            pool.submit(
                _fit_one,
                risk,
                x_train,
                train[f"y_{risk}"].to_numpy(dtype=np.float32),
                x_valid,
                valid[f"y_{risk}"].to_numpy(dtype=np.float32),
                threads_each,
            ): risk
            for risk in RISKS
        }
        for fut in as_completed(futs):
            risk = futs[fut]
            boosters[risk] = fut.result()
            print(f"  готово {risk}: trees={boosters[risk].best_iteration}", flush=True)

    def _predict_split(frame: pd.DataFrame) -> dict[str, np.ndarray]:
        with ThreadPoolExecutor(max_workers=len(RISKS)) as pool:
            futs = {pool.submit(boosters[risk].predict, frame): risk for risk in RISKS}
            return {futs[fut]: fut.result() for fut in as_completed(futs)}

    lgbm_valid = _predict_split(x_valid)
    lgbm_test = _predict_split(x_test)
    lgbm_thr = {}
    for risk in RISKS:
        tgt = TARGET_METRICS[risk]
        lgbm_thr[risk] = choose_threshold(
            valid[f"y_{risk}"].to_numpy(dtype=np.int32),
            lgbm_valid[risk],
            min_recall=tgt["min_recall"],
            min_precision=tgt["min_precision"],
            strategy=tgt["strategy"],
        )

    print("valid:", flush=True)
    valid_report = {
        "persist": evaluate_all("val", valid, persist_valid, persist_thr),
        "rules": evaluate_all("val", valid, rules_valid, rules_thr),
        "lgbm": evaluate_all("val", valid, lgbm_valid, lgbm_thr),
    }
    print("test:", flush=True)
    test_report = {
        "persist": evaluate_all("test", test, persist_test, persist_thr),
        "rules": evaluate_all("test", test, rules_test, rules_thr),
        "lgbm": evaluate_all("test", test, lgbm_test, lgbm_thr),
    }

    rng = np.random.default_rng(RNG)
    n_sample = min(args.shap_sample, len(valid))
    sample_idx = rng.choice(len(valid), size=n_sample, replace=False)
    sample = x_valid.iloc[sample_idx]
    print(f"SHAP sample={n_sample}", flush=True)
    shap_report: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=len(RISKS)) as pool:
        futs = {
            pool.submit(shap_top, boosters[risk], sample, risk, threads_each): risk
            for risk in RISKS
        }
        for fut in as_completed(futs):
            risk = futs[fut]
            shap_report[risk] = fut.result()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for risk, booster in boosters.items():
        dest = MODELS_DIR / f"lgbm_{risk}.txt"
        booster.save_model(str(dest))
    payload = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "threads": threads,
        "threads_per_model": threads_each,
        "n_train": int(len(train)),
        "n_valid": int(len(valid)),
        "n_test": int(len(test)),
        "features": feats,
        "thresholds": {
            "persist": persist_thr,
            "rules": rules_thr,
            "lgbm": lgbm_thr,
        },
        "valid": valid_report,
        "test": test_report,
        "shap": shap_report,
        "recommendations": RECOMMENDATIONS,
        "models_dir": str(MODELS_DIR),
    }
    metrics_path = MODELS_DIR / "metrics.json"
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"metrics: {metrics_path}", flush=True)
    write_docs(payload)

    print("сравнение PR-AUC test: LightGBM vs persist", flush=True)
    for risk in RISKS:
        a = test_report["lgbm"][risk]["pr_auc"]
        b = test_report["persist"][risk]["pr_auc"]
        flag = "OK" if a >= b else "WEAK"
        print(f"  {risk:10} lgbm={a:.3f} persist={b:.3f} {flag}", flush=True)


if __name__ == "__main__":
    main()
