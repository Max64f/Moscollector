"""Конвертация журналов CSV → Parquet с нормализацией значений."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from common import DATA_DIR, PARQUET_DIR, duck_path, normalized_events_sql


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=4")
    try:
        con.execute("SET memory_limit='6GB'")
    except duckdb.Error:
        pass
    return con


def convert_file(con: duckdb.DuckDBPyConnection, src: Path, dest: Path, force: bool) -> dict:
    if dest.exists() and not force:
        print(f"skip {src.name}: {dest.name} уже есть", flush=True)
        return {"file": src.name, "parquet": dest.name, "skipped": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.parquet")
    if tmp.exists():
        tmp.unlink()
    print(f"parquet {src.name} → {dest.name} ({src.stat().st_size / 1e6:.1f} MB)...", flush=True)
    sql = normalized_events_sql(src)
    con.execute(
        f"""
        COPY ({sql}) TO '{duck_path(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    tmp.replace(dest)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{duck_path(dest)}')").fetchone()[0]
    print(f"  готово: {n} строк, {dest.stat().st_size / 1e6:.1f} MB", flush=True)
    return {"file": src.name, "parquet": dest.name, "rows": int(n), "skipped": False}


def sources(only: set[str] | None) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    example = DATA_DIR / "журнал_событий_пример.csv"
    if example.exists() and (only is None or "example" in only):
        items.append(("example", example))
    for path in sorted(DATA_DIR.glob("ext-journal-*.csv")):
        year = path.stem.replace("ext-journal-", "")
        if only is None or year in only:
            items.append((year, path))
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", default="", help="example,2019,2026")
    args = parser.parse_args()
    only = {part.strip() for part in args.only.split(",") if part.strip()} or None
    if not DATA_DIR.exists():
        raise SystemExit(f"DATA_DIR не найден: {DATA_DIR}")
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    results = []
    for name, path in sources(only):
        dest = PARQUET_DIR / f"events_{name}.parquet"
        results.append(convert_file(con, path, dest, args.force))
    if not results:
        raise SystemExit("Нет CSV для конвертации")
    print(f"PARQUET_DIR={PARQUET_DIR}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
