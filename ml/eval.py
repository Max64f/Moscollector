"""Метрики и выбор порога на валидации."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)


def safe_average_precision(y_true: np.ndarray, y_score: np.ndarray) -> float:
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        return 0.0
    return float(average_precision_score(y_true, y_score))


def metrics_at_threshold(y_true: np.ndarray, y_score: np.ndarray, threshold: float) -> dict:
    pred = (y_score >= threshold).astype(np.int32)
    return {
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "positives_pred": int(pred.sum()),
        "positives_true": int(y_true.sum()),
        "n": int(len(y_true)),
        "pr_auc": safe_average_precision(y_true, y_score),
    }


def choose_threshold(
    y_true: np.ndarray,
    y_score: np.ndarray,
    *,
    min_recall: float,
    min_precision: float,
    strategy: str,
) -> float:
    """Порог по PR-кривой valid. recall_first / precision_first — из целевых метрик ТЗ."""
    precision, recall, thresholds = precision_recall_curve(y_true, y_score)
    if len(thresholds) == 0:
        return 0.5
    # precision/recall на 1 длиннее thresholds
    precision = precision[:-1]
    recall = recall[:-1]
    if strategy == "precision_first":
        mask = precision >= min_precision
        if mask.any():
            idx = np.where(mask)[0][np.argmax(recall[mask])]
            return float(thresholds[idx])
        idx = int(np.argmax(precision))
        return float(thresholds[idx])
    mask = recall >= min_recall
    if mask.any():
        idx = np.where(mask)[0][np.argmax(precision[mask])]
        return float(thresholds[idx])
    idx = int(np.argmax(recall))
    return float(thresholds[idx])
