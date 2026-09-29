"""Проверка собранного датасета: заполненность, утечки, базовая разделяющая сила."""

from __future__ import annotations

import argparse

import duckdb

from ml.config import FEATURES_DIR, RISKS
from ml.features import duck_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", default="2026")
    args = parser.parse_args()
    path = FEATURES_DIR / f"features_{args.year}.parquet"
    if not path.exists():
        raise SystemExit(f"нет файла {path}")
    src = duck_path(path)
    con = duckdb.connect()

    print("=== колонки ===")
    cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()
    print(f"всего {len(cols)}: {', '.join(c[0] for c in cols)}")

    print("\n=== заполненность ключевых признаков ===")
    checks = [
        "temp_out",
        "precip",
        "humidity",
        "temp_mean_24h",
        "gas_max_24h",
        "temp_trend",
        "events_per_channel_24h",
    ]
    total = con.execute(f"SELECT COUNT(*) FROM read_parquet('{src}')").fetchone()[0]
    for col in checks:
        n = con.execute(
            f"SELECT COUNT({col}) FROM read_parquet('{src}')"
        ).fetchone()[0]
        print(f"  {col:26} {n}/{total} = {100.0 * n / total:.1f}%")

    print("\n=== доли положительных лейблов ===")
    for risk in RISKS:
        row = con.execute(
            f"""
            SELECT AVG(y_{risk})::DOUBLE, SUM(y_{risk}), MAX(y_{risk}_cnt)
            FROM read_parquet('{src}')
            """
        ).fetchone()
        print(f"  {risk:10} rate={row[0]:.4f} pos={row[1]} max_cnt={row[2]}")

    print("\n=== разделяющая сила: среднее признака при y=0 и y=1 ===")
    for risk in RISKS:
        print(f"  -- {risk} --")
        for col in (f"{risk}_24h", f"{risk}_6h", "al_24h", "ev_24h"):
            row = con.execute(
                f"""
                SELECT
                    AVG(CASE WHEN y_{risk} = 0 THEN {col} END),
                    AVG(CASE WHEN y_{risk} = 1 THEN {col} END)
                FROM read_parquet('{src}')
                """
            ).fetchone()
            neg = row[0] or 0.0
            pos = row[1] or 0.0
            print(f"     {col:16} y=0: {neg:10.2f}   y=1: {pos:10.2f}")

    print("\n=== проверка на утечку: совпадает ли лейбл с текущим часом ===")
    for risk in RISKS:
        source = {"fire": "fire_1h", "flood": "flood_1h", "failure": "failure_1h", "intrusion": "intrusion_1h"}[risk]
        row = con.execute(
            f"""
            SELECT
                AVG(CASE WHEN {source} > 0 THEN y_{risk} END),
                AVG(CASE WHEN {source} = 0 THEN y_{risk} END)
            FROM read_parquet('{src}')
            """
        ).fetchone()
        now_pos = row[0] if row[0] is not None else float("nan")
        now_zero = row[1] if row[1] is not None else float("nan")
        print(f"  {risk:10} P(y=1 | сейчас есть)={now_pos:.3f}   P(y=1 | сейчас нет)={now_zero:.3f}")

    print("\n=== пример строки с пожаром ===")
    row = con.execute(
        f"""
        SELECT object_id, hour_ts, fire_24h, fire_pickets_6h, temp_max_24h,
               gas_max_24h, temp_out, precip, y_fire, y_fire_cnt
        FROM read_parquet('{src}')
        WHERE y_fire = 1
        ORDER BY y_fire_cnt DESC
        LIMIT 3
        """
    ).fetchall()
    for item in row:
        print(f"  {item}")

    print("\n=== пример строки с подтоплением ===")
    row = con.execute(
        f"""
        SELECT object_id, hour_ts, pump_on_24h, flood_24h, precip, temp_out, y_flood, y_flood_cnt
        FROM read_parquet('{src}')
        WHERE y_flood = 1
        ORDER BY y_flood_cnt DESC
        LIMIT 3
        """
    ).fetchall()
    for item in row:
        print(f"  {item}")


if __name__ == "__main__":
    main()
