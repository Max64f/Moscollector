"""Загрузка исторической погоды Москвы из Open-Meteo в Parquet и Postgres.

Нужна для прогноза подтопления: осадки объясняют всплески работы насосов.
Если внешний API недоступен, скрипт завершается с ошибкой, но пайплайн признаков
умеет работать и без метео (колонки будут NULL).
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import date

import duckdb
import psycopg

from common import DATABASE_URL, PARQUET_DIR, duck_path, psycopg_dsn

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173
DAILY_FIELDS = "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean"


def fetch(start: date, end: date) -> list[dict]:
    query = (
        f"{ARCHIVE_URL}?latitude={MOSCOW_LAT}&longitude={MOSCOW_LON}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&daily={DAILY_FIELDS}&timezone=Europe%2FMoscow"
    )
    print(f"GET {query}", flush=True)
    with urllib.request.urlopen(query, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    daily = payload.get("daily") or {}
    days = daily.get("time") or []
    if not days:
        raise SystemExit(f"Open-Meteo не вернул данные: {payload.get('reason', payload)}")
    rows = []
    for index, day in enumerate(days):
        rows.append(
            {
                "day": day,
                "temp_mean": _at(daily.get("temperature_2m_mean"), index),
                "precipitation_mm": _at(daily.get("precipitation_sum"), index),
                "humidity": _at(daily.get("relative_humidity_2m_mean"), index),
            }
        )
    return rows


def _at(values: list | None, index: int):
    if not values or index >= len(values):
        return None
    return values[index]


def write_parquet(rows: list[dict]) -> None:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    dest = PARQUET_DIR / "meteo_daily.parquet"
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE meteo (
            day DATE,
            temp_mean DOUBLE,
            precipitation_mm DOUBLE,
            humidity DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO meteo VALUES (?, ?, ?, ?)",
        [(r["day"], r["temp_mean"], r["precipitation_mm"], r["humidity"]) for r in rows],
    )
    con.execute(f"COPY meteo TO '{duck_path(dest)}' (FORMAT PARQUET)")
    print(f"parquet: {dest} ({len(rows)} дней)", flush=True)


def write_postgres(rows: list[dict]) -> None:
    dsn = psycopg_dsn(DATABASE_URL)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE meteo_daily")
        cur.executemany(
            """
            INSERT INTO meteo_daily (day, temp_mean, precipitation_mm, humidity)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (day) DO UPDATE SET
                temp_mean = EXCLUDED.temp_mean,
                precipitation_mm = EXCLUDED.precipitation_mm,
                humidity = EXCLUDED.humidity
            """,
            [(r["day"], r["temp_mean"], r["precipitation_mm"], r["humidity"]) for r in rows],
        )
        conn.commit()
    print(f"postgres: meteo_daily {len(rows)} дней", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--skip-postgres", action="store_true")
    args = parser.parse_args()
    rows = fetch(date.fromisoformat(args.start), date.fromisoformat(args.end))
    write_parquet(rows)
    if not args.skip_postgres:
        write_postgres(rows)


if __name__ == "__main__":
    main()
