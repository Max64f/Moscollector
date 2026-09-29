"""Разовая разведка: какие категориальные значения бывают у каждого типа датчика."""

from __future__ import annotations

import duckdb

from common import DATA_DIR, PARQUET_DIR, duck_path

con = duckdb.connect()
con.execute("PRAGMA threads=4")
con.execute("SET memory_limit='6GB'")

channels = duck_path(DATA_DIR / "справочник_каналов_датчиков.csv")
events = duck_path(PARQUET_DIR / "events_2026.parquet")

rows = con.execute(
    f"""
    SELECT c.тип_датчика AS sensor_type, e.value_cat, e.is_alarm, COUNT(*) AS n
    FROM read_parquet('{events}') e
    JOIN read_csv('{channels}', header=true, auto_detect=true) c
        ON e.channel_id = TRY_CAST(c.ид_канала_данных AS BIGINT)
    WHERE e.value_cat IS NOT NULL
    GROUP BY 1, 2, 3
    HAVING COUNT(*) > 50
    ORDER BY sensor_type, n DESC
    """
).fetchall()

current = None
for sensor_type, value, alarm, n in rows:
    if sensor_type != current:
        print(f"\n=== {sensor_type} ===")
        current = sensor_type
    flag = "ALARM" if alarm else "     "
    print(f"  {flag} {value!r:40} {n}")

print("\n=== числовые диапазоны по типам ===")
num = con.execute(
    f"""
    SELECT c.тип_датчика, COUNT(*) AS n,
           MIN(e.value_num), AVG(e.value_num), MAX(e.value_num)
    FROM read_parquet('{events}') e
    JOIN read_csv('{channels}', header=true, auto_detect=true) c
        ON e.channel_id = TRY_CAST(c.ид_канала_данных AS BIGINT)
    WHERE e.value_num IS NOT NULL
    GROUP BY 1
    ORDER BY n DESC
    """
).fetchall()
for sensor_type, n, vmin, vavg, vmax in num:
    print(f"  {sensor_type:30} n={n:10} min={vmin} avg={vavg:.2f} max={vmax}")
