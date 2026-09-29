"""Классификация событий СМВУ и параметры построения датасета.

Словари ниже собраны по фактическим значениям журнала (см. docs/data_profile.md).
Одно и то же значение значит разное в зависимости от типа датчика:
`Не замкнут` у теплового датчика — пожар, у КД Дверь — вскрытие,
у датчика затопления — вода.
"""

from __future__ import annotations

import os
from pathlib import Path

PARQUET_DIR = Path(os.environ.get("PARQUET_DIR", "/parquet"))
FEATURES_DIR = PARQUET_DIR / "features"
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(PARQUET_DIR / "models")))
DOCS_DIR = Path(os.environ.get("DOCS_DIR", "/docs"))

HISTORY_HOURS = 24
HORIZON_HOURS = 24

TRAIN_YEARS = ("2019", "2020", "2021", "2022", "2023", "2024")
VALID_YEARS = ("2025",)
TEST_YEARS = ("2026",)

RISKS = ("fire", "flood", "failure", "intrusion")

ID_COLUMNS = ("object_id", "hour_ts", "day")
LABEL_COLUMNS = tuple(f"y_{risk}" for risk in RISKS) + tuple(f"y_{risk}_cnt" for risk in RISKS)

# Пожар/подтопление: лучше лишний осмотр. НСД: не прятать реальный доступ,
# но и не заваливать диспетчера ложными. Подбираем порог на valid.
TARGET_METRICS = {
    "fire": {"min_recall": 0.70, "min_precision": 0.30, "strategy": "recall_first"},
    "flood": {"min_recall": 0.70, "min_precision": 0.30, "strategy": "recall_first"},
    "failure": {"min_recall": 0.60, "min_precision": 0.40, "strategy": "recall_first"},
    "intrusion": {"min_recall": 0.50, "min_precision": 0.80, "strategy": "precision_first"},
}

RECOMMENDATIONS = {
    "fire": "Проверить дымовые, тепловые и газовые датчики на пикетах объекта. При нескольких пикетах — выезд бригады.",
    "flood": "Проверить насосы и датчики затопления, сверить с осадками. При «Затоплен» — выезд на объект.",
    "failure": "Запланировать ТО: канал даёт отказ или дребезг. Сверить питание/ИБП, не трактовать как инцидент.",
    "intrusion": "Верифицировать НСД: режим охраны, соседние контактные датчики, камеры. Одиночная сработка чаще ложная.",
}

RISK_TITLES = {
    "fire": "Пожар",
    "flood": "Подтопление",
    "failure": "Отказ датчика",
    "intrusion": "НСД",
}

# (тип_датчика, значение) -> событие пожара
FIRE_PAIRS = (
    ("Датчик дыма", "Обнаружен дым"),
    ("Газовый датчик", "Обнаружен газ"),
    ("Тепловой датчик", "Не замкнут"),
    ("Ручной извещатель", "Не замкнут"),
)

FLOOD_PAIRS = (
    ("Состояние насоса", "Затоплен"),
    ("Состояние насоса", "Работают все насосы в АНС"),
    ("Датчик затопления", "Не замкнут"),
)

# Отказ оборудования: значение не зависит от типа датчика.
FAILURE_VALUES = (
    "Неисправен",
    "Обесточен",
    "Отключено устройство",
    "Питание от батарей",
    "Много неисправных устройств",
    "Не определено",
)

# Несанкционированный доступ: тревога на охранном датчике.
INTRUSION_TYPES = (
    "Датчик движения",
    "КД АВ",
    "КД Дверь",
    "КД Люк",
    "Стекло",
    "9-секционный люк",
)
INTRUSION_PAIRS = (("Состояние УИР-Р", "Рычаг сдернут"),)

# Типы, по которым считаем числовые агрегаты температуры.
TEMP_TYPES = ("Датчик температуры",)
GAS_TYPES = ("Газовый датчик",)
PUMP_TYPE = "Состояние насоса"
FAN_TYPE = "Состояние вентилятора"

# Физически невозможные показания термодатчика -> признак отказа, не холода.
TEMP_VALID_MIN = -45.0
TEMP_VALID_MAX = 90.0

GUARD_ON = "На охране"
GUARD_OFF = "Снято с охраны"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pairs_condition(pairs: tuple[tuple[str, str], ...]) -> str:
    """SQL-условие по парам (тип датчика, значение)."""
    if not pairs:
        return "FALSE"
    items = ", ".join(f"({_quote(t)}, {_quote(v)})" for t, v in pairs)
    return f"(sensor_type, value_cat) IN ({items})"


def values_condition(values: tuple[str, ...]) -> str:
    if not values:
        return "FALSE"
    items = ", ".join(_quote(v) for v in values)
    return f"value_cat IN ({items})"


def types_condition(types: tuple[str, ...]) -> str:
    if not types:
        return "FALSE"
    items = ", ".join(_quote(t) for t in types)
    return f"sensor_type IN ({items})"


def intrusion_condition() -> str:
    """Охранная тревога: тип из охранных И флаг тревоги, либо сдёрнутый рычаг."""
    guarded = f"({types_condition(INTRUSION_TYPES)} AND is_alarm)"
    return f"({guarded} OR {pairs_condition(INTRUSION_PAIRS)})"
