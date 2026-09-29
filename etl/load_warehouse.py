"""Загрузка справочников и витрин в PostgreSQL из CSV/Parquet."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import duckdb
import psycopg

from common import (
    DATA_DIR,
    DATABASE_URL,
    HOURLY_SENSOR_TYPES,
    PARQUET_DIR,
    duck_path,
    psycopg_dsn,
)


def connect_duck() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=4")
    try:
        con.execute("SET memory_limit='6GB'")
    except duckdb.Error:
        pass
    return con


def _temp_csv() -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    handle.close()
    return Path(handle.name)


def copy_csv(conn: psycopg.Connection, table: str, columns: list[str], csv_path: Path) -> None:
    cols = ", ".join(columns)
    with conn.cursor() as cur, csv_path.open("r", encoding="utf-8") as handle:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')") as copy:
            while chunk := handle.read(1024 * 1024):
                copy.write(chunk)
    conn.commit()


def load_objects(con: duckdb.DuckDBPyConnection, conn: psycopg.Connection) -> int:
    src = duck_path(DATA_DIR / "справочник_объектов_диспетчер.csv")
    tmp = _temp_csv()
    try:
        con.execute(
            f"""
            COPY (
                WITH src AS (
                    SELECT
                        TRY_CAST(ид_объект AS BIGINT) AS id,
                        TRY_CAST(иерархия_уровень AS INTEGER) AS level,
                        TRY_CAST(родитель AS BIGINT) AS parent_id,
                        вид_объекта AS kind,
                        диспетчерское_название_объекта AS name
                    FROM read_csv('{src}', header=true, auto_detect=true)
                )
                SELECT
                    id,
                    level,
                    CASE WHEN parent_id IN (SELECT id FROM src) THEN parent_id END AS parent_id,
                    kind,
                    name
                FROM src
                ORDER BY level, id
            ) TO '{duck_path(tmp)}' (HEADER, DELIMITER ',')
            """
        )
        with conn.cursor() as cur:
            cur.execute("TRUNCATE objects CASCADE")
        conn.commit()
        copy_csv(conn, "objects", ["id", "level", "parent_id", "kind", "name"], tmp)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM objects")
            return int(cur.fetchone()[0])
    finally:
        tmp.unlink(missing_ok=True)


def load_channels(con: duckdb.DuckDBPyConnection, conn: psycopg.Connection) -> int:
    src = duck_path(DATA_DIR / "справочник_каналов_датчиков.csv")
    tmp = _temp_csv()
    try:
        con.execute(
            f"""
            COPY (
                SELECT
                    TRY_CAST(ид_канала_данных AS BIGINT) AS id,
                    тип_инж_системы AS system_type,
                    тип_датчика AS sensor_type,
                    тег_инженерной_системы AS tag,
                    название_датчика AS name,
                    TRY_CAST(ид_объект AS BIGINT) AS object_id,
                    regexp_extract(название_датчика, 'ПК\\s*(\\d+(?:[.,+]\\d+)*)', 1) AS picket
                FROM read_csv('{src}', header=true, auto_detect=true)
            ) TO '{duck_path(tmp)}' (HEADER, DELIMITER ',')
            """
        )
        with conn.cursor() as cur:
            cur.execute("TRUNCATE channels CASCADE")
        conn.commit()
        copy_csv(
            conn,
            "channels",
            ["id", "system_type", "sensor_type", "tag", "name", "object_id", "picket"],
            tmp,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM channels")
            return int(cur.fetchone()[0])
    finally:
        tmp.unlink(missing_ok=True)


def parquet_files() -> list[Path]:
    files = sorted(PARQUET_DIR.glob("events_*.parquet"))
    return [path for path in files if path.stat().st_size > 0]


def load_alarms(con: duckdb.DuckDBPyConnection, conn: psycopg.Connection) -> int:
    files = parquet_files()
    if not files:
        raise SystemExit(f"Нет parquet в {PARQUET_DIR}. Сначала python to_parquet.py")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE events_alarm")
        cur.execute("DROP TABLE IF EXISTS alarms_stage")
        cur.execute(
            """
            CREATE UNLOGGED TABLE alarms_stage (
                event_id BIGINT,
                channel_id BIGINT,
                event_ts TIMESTAMP,
                value_raw TEXT,
                value_num DOUBLE PRECISION,
                value_cat VARCHAR(255)
            )
            """
        )
    conn.commit()
    for path in files:
        tmp = _temp_csv()
        print(f"alarms {path.name}...", flush=True)
        try:
            con.execute(
                f"""
                COPY (
                    SELECT event_id, channel_id, event_ts, value_raw, value_num, value_cat
                    FROM read_parquet('{duck_path(path)}')
                    WHERE is_alarm AND event_id IS NOT NULL AND event_ts IS NOT NULL
                    QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY event_ts) = 1
                ) TO '{duck_path(tmp)}' (HEADER, DELIMITER ',')
                """
            )
            copy_csv(
                conn,
                "alarms_stage",
                ["event_id", "channel_id", "event_ts", "value_raw", "value_num", "value_cat"],
                tmp,
            )
            print(f"  stage {path.name}", flush=True)
        finally:
            tmp.unlink(missing_ok=True)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events_alarm (event_id, channel_id, event_ts, value_raw, value_num, value_cat)
            SELECT DISTINCT ON (event_id)
                event_id, channel_id, event_ts, value_raw, value_num, value_cat
            FROM alarms_stage
            ORDER BY event_id, event_ts
            """
        )
        cur.execute("SELECT COUNT(*) FROM events_alarm")
        total = int(cur.fetchone()[0])
        cur.execute("DROP TABLE IF EXISTS alarms_stage")
    conn.commit()
    return total


def load_hourly(con: duckdb.DuckDBPyConnection, conn: psycopg.Connection) -> int:
    files = parquet_files()
    types_sql = ", ".join(f"'{item}'" for item in HOURLY_SENSOR_TYPES)
    channels_csv = duck_path(DATA_DIR / "справочник_каналов_датчиков.csv")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE events_numeric_hourly")
    conn.commit()
    total = 0
    for path in files:
        tmp = _temp_csv()
        print(f"hourly {path.name}...", flush=True)
        try:
            con.execute(
                f"""
                COPY (
                    SELECT
                        e.channel_id,
                        date_trunc('hour', e.event_ts) AS hour_ts,
                        COUNT(*)::INTEGER AS n_events,
                        SUM(CAST(e.is_alarm AS INTEGER))::INTEGER AS n_alarms,
                        AVG(e.value_num) AS value_mean,
                        MIN(e.value_num) AS value_min,
                        MAX(e.value_num) AS value_max
                    FROM read_parquet('{duck_path(path)}') e
                    INNER JOIN read_csv('{channels_csv}', header=true, auto_detect=true) c
                        ON e.channel_id = TRY_CAST(c.ид_канала_данных AS BIGINT)
                    WHERE e.event_ts IS NOT NULL
                      AND c.тип_датчика IN ({types_sql})
                    GROUP BY 1, 2
                ) TO '{duck_path(tmp)}' (HEADER, DELIMITER ',')
                """
            )
            n = con.execute(
                f"SELECT COUNT(*) FROM read_csv('{duck_path(tmp)}', header=true, auto_detect=true)"
            ).fetchone()[0]
            copy_csv(
                conn,
                "events_numeric_hourly",
                ["channel_id", "hour_ts", "n_events", "n_alarms", "value_mean", "value_min", "value_max"],
                tmp,
            )
            total += int(n)
            print(f"  +{n} часов", flush=True)
        finally:
            tmp.unlink(missing_ok=True)
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-hourly", action="store_true")
    parser.add_argument("--skip-alarms", action="store_true")
    args = parser.parse_args()
    dsn = psycopg_dsn(DATABASE_URL)
    print(f"DATA_DIR={DATA_DIR}", flush=True)
    print(f"PARQUET_DIR={PARQUET_DIR}", flush=True)
    print(f"DB={dsn}", flush=True)
    con = connect_duck()
    with psycopg.connect(dsn) as conn:
        n_obj = load_objects(con, conn)
        print(f"objects: {n_obj}", flush=True)
        n_ch = load_channels(con, conn)
        print(f"channels: {n_ch}", flush=True)
        n_alarms = 0 if args.skip_alarms else load_alarms(con, conn)
        print(f"alarms: {n_alarms}", flush=True)
        n_hourly = 0 if args.skip_hourly else load_hourly(con, conn)
        print(f"hourly: {n_hourly}", flush=True)


if __name__ == "__main__":
    main()
