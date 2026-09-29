"""Справочники рисков и эвристики — те же пары, что в ml/config.py."""

from __future__ import annotations

RISKS = ("fire", "flood", "failure", "intrusion")

RISK_TITLES = {
    "fire": "Пожар",
    "flood": "Подтопление",
    "failure": "Отказ датчика",
    "intrusion": "НСД",
}

RECOMMENDATIONS = {
    "fire": "Проверить дымовые, тепловые и газовые датчики на пикетах объекта. При нескольких пикетах — выезд бригады.",
    "flood": "Проверить насосы и датчики затопления, сверить с осадками. При «Затоплен» — выезд на объект.",
    "failure": "Запланировать ТО: канал даёт отказ или дребезг. Сверить питание/ИБП, не трактовать как инцидент.",
    "intrusion": "Верифицировать НСД: режим охраны, соседние контактные датчики, камеры. Одиночная сработка чаще ложная.",
}

DECISIONS = ("dispatch", "false_alarm", "watch", "maintenance")

DECISION_TITLES = {
    "dispatch": "Выезд",
    "false_alarm": "Ложное",
    "watch": "Наблюдение",
    "maintenance": "ТО",
}

FIRE_PAIRS = {
    ("Датчик дыма", "Обнаружен дым"),
    ("Газовый датчик", "Обнаружен газ"),
    ("Тепловой датчик", "Не замкнут"),
    ("Ручной извещатель", "Не замкнут"),
}

FLOOD_PAIRS = {
    ("Состояние насоса", "Затоплен"),
    ("Состояние насоса", "Работают все насосы в АНС"),
    ("Датчик затопления", "Не замкнут"),
}

FAILURE_VALUES = {
    "Неисправен",
    "Обесточен",
    "Отключено устройство",
    "Питание от батарей",
    "Много неисправных устройств",
    "Не определено",
}

INTRUSION_TYPES = {
    "Датчик движения",
    "КД АВ",
    "КД Дверь",
    "КД Люк",
    "Стекло",
    "9-секционный люк",
}

HORIZON_HOURS = 24


def classify_event(sensor_type: str | None, value_cat: str | None) -> str | None:
    if not sensor_type:
        return None
    pair = (sensor_type, value_cat or "")
    if pair in FIRE_PAIRS:
        return "fire"
    if pair in FLOOD_PAIRS:
        return "flood"
    if value_cat in FAILURE_VALUES:
        return "failure"
    if sensor_type in INTRUSION_TYPES or pair == ("Состояние УИР-Р", "Рычаг сдернут"):
        return "intrusion"
    return None
