"""Сборка обучающего датасета: Parquet журналов -> признаки + лейблы по годам."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import duckdb

from ml.config import FEATURES_DIR, PARQUET_DIR, RISKS
from ml.features import build_dataset_sql, duck_path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CHANNELS_CSV = DATA_DIR / "справочник_каналов_датчиков.csv"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=4")
    try:
        con.execute("SET memory_limit='6GB'")
    except duckdb.Error:
        pass
    con.execute(f"SET temp_directory='{duck_path(PARQUET_DIR)}/duckdb_tmp'")
    return con


def year_sources(only: set[str] | None) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for path in sorted(PARQUET_DIR.glob("events_*.parquet")):
        name = path.stem.replace("events_", "")
        if name == "example":
            continue
        if only is None or name in only:
            items.append((name, path))
    return items


def build_year(con: duckdb.DuckDBPyConnection, year: str, events: Path, force: bool) -> dict:
    dest = FEATURES_DIR / f"features_{year}.parquet"
    if dest.exists() and not force:
        print(f"skip {year}: {dest.name} уже есть", flush=True)
        return {"year": year, "skipped": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    meteo = PARQUET_DIR / "meteo_daily.parquet"
    sql = build_dataset_sql(events, CHANNELS_CSV, meteo if meteo.exists() else None)
    print(f"features {year}: {events.name} -> {dest.name}...", flush=True)
    tmp = dest.with_suffix(".tmp.parquet")
    if tmp.exists():
        tmp.unlink()
    con.execute(f"COPY ({sql}) TO '{duck_path(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    tmp.replace(dest)

    stats_cols = ", ".join(f"SUM(y_{risk}) AS pos_{risk}" for risk in RISKS)
    row = con.execute(
        f"""
        SELECT COUNT(*) AS n_rows,
               COUNT(DISTINCT object_id) AS n_objects,
               {stats_cols}
        FROM read_parquet('{duck_path(dest)}')
        """
    ).fetchone()
    stats = {
        "year": year,
        "rows": int(row[0]),
        "objects": int(row[1]),
        "skipped": False,
    }
    for index, risk in enumerate(RISKS, start=2):
        positives = int(row[index] or 0)
        stats[f"pos_{risk}"] = positives
        stats[f"rate_{risk}"] = round(positives / row[0], 5) if row[0] else 0.0
    print(f"  {stats}", flush=True)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", default="", help="например 2025,2026")
    args = parser.parse_args()
    only = {part.strip() for part in args.only.split(",") if part.strip()} or None

    sources = year_sources(only)
    if not sources:
        raise SystemExit(f"Нет events_*.parquet в {PARQUET_DIR}")

    con = connect()
    report = [build_year(con, year, path, args.force) for year, path in sources]
    summary = FEATURES_DIR / "summary.json"
    existing = {}
    if summary.exists():
        existing = {item["year"]: item for item in json.loads(summary.read_text(encoding="utf-8"))}
    for item in report:
        if not item.get("skipped"):
            existing[item["year"]] = item
    summary.write_text(
        json.dumps(sorted(existing.values(), key=lambda x: x["year"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"summary: {summary}", flush=True)


if __name__ == "__main__":
    main()
