"""Общие пути и SQL нормализации журнала СМВУ."""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PARQUET_DIR = Path(os.environ.get("PARQUET_DIR", "/parquet"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ldt:ldt@db:5432/moscollector",
)

HOURLY_SENSOR_TYPES = (
    "Датчик температуры",
    "Тепловой датчик",
    "Датчик затопления",
    "Состояние насоса",
    "Состояние вентилятора",
)


def duck_path(path: Path) -> str:
    return path.resolve().as_posix()


def psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def normalized_events_sql(csv_path: Path) -> str:
    src = duck_path(csv_path)
    return f"""
        SELECT
            TRY_CAST(ид_события AS BIGINT) AS event_id,
            TRY_CAST(ид_канала_данных AS BIGINT) AS channel_id,
            TRY_CAST(дата AS DATE)
                + TRY_CAST(CAST(время AS VARCHAR) AS TIME) AS event_ts,
            lower(CAST(тревожное AS VARCHAR)) IN ('t', 'true', '1') AS is_alarm,
            CAST(значение_датчика AS VARCHAR) AS value_raw,
            CASE
                WHEN CAST(значение_датчика AS VARCHAR) LIKE '%01.01.1970%' THEN NULL
                ELSE TRY_CAST(replace(CAST(значение_датчика AS VARCHAR), ',', '.') AS DOUBLE)
            END AS value_num,
            CASE
                WHEN CAST(значение_датчика AS VARCHAR) LIKE '%01.01.1970%' THEN NULL
                WHEN TRY_CAST(replace(CAST(значение_датчика AS VARCHAR), ',', '.') AS DOUBLE) IS NOT NULL THEN NULL
                ELSE CAST(значение_датчика AS VARCHAR)
            END AS value_cat
        FROM read_csv(
            '{src}',
            header=true,
            auto_detect=true,
            ignore_errors=true,
            sample_size=200000
        )
        WHERE TRY_CAST(ид_события AS BIGINT) IS NOT NULL
          AND TRY_CAST(ид_канала_данных AS BIGINT) IS NOT NULL
    """
