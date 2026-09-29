"""Правила без ML: персистентность и простые эвристики диспетчера."""

from __future__ import annotations

import numpy as np
import pandas as pd


def persist_score(frame: pd.DataFrame, risk: str) -> np.ndarray:
    """Непрерывный baseline: чем больше таких событий за сутки, тем выше риск."""
    col = f"{risk}_24h"
    values = frame[col].to_numpy(dtype=np.float64, copy=False)
    return np.clip(values / (values + 3.0), 0.0, 1.0)


def rule_score(frame: pd.DataFrame, risk: str) -> np.ndarray:
    """Булевы правила → {0, 1}. Годятся как нижняя граница для LightGBM."""
    if risk == "fire":
        fire = frame["fire_24h"].fillna(0) > 0
        multi = (frame["fire_pickets_6h"].fillna(0) >= 2) | (frame["fire_6h"].fillna(0) >= 2)
        hot = frame["temp_max_6h"].fillna(0) >= 40
        gas = frame["gas_max_6h"].fillna(0) >= 5
        return (fire | (multi & (hot | gas))).to_numpy(dtype=np.float64)
    if risk == "flood":
        water = frame["flood_24h"].fillna(0) > 0
        pumps = (frame["pump_on_24h"].fillna(0) >= 8) & (frame["precip"].fillna(0) >= 3)
        return (water | pumps).to_numpy(dtype=np.float64)
    if risk == "failure":
        fresh = frame["failure_new_24h"].fillna(0) > 0
        chatter = frame["chatter_max_24h"].fillna(0) >= 40
        many = frame["failure_24h"].fillna(0) >= 5
        return (fresh | chatter | many).to_numpy(dtype=np.float64)
    if risk == "intrusion":
        alarm = frame["intrusion_24h"].fillna(0) > 0
        guarded = frame["guard_on"].fillna(0) >= 1
        neighbors = frame["intrusion_channels_6h"].fillna(0) >= 2
        return ((alarm & guarded) | neighbors).to_numpy(dtype=np.float64)
    raise ValueError(risk)
