"""Профиль датасета СМВУ: справочники, пример журнала, годовые файлы."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import duckdb

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[1] if len(_HERE.parents) >= 2 else _HERE.parent
_DEFAULT_DATA = _HERE.parents[2] / "dataset" if len(_HERE.parents) >= 3 else Path("/data")
DATA_DIR = Path(os.environ.get("DATA_DIR", _DEFAULT_DATA))
OUTPUT_MD = Path(os.environ.get("OUTPUT_MD", _ROOT / "docs" / "data_profile.md"))
OUTPUT_JSON = Path(os.environ.get("OUTPUT_JSON", _HERE.parent / "output" / "profile.json"))

OBJECTS_NAME = "справочник_объектов_диспетчер.csv"
CHANNELS_NAME = "справочник_каналов_датчиков.csv"
EXAMPLE_NAME = "журнал_событий_пример.csv"

EPOCH_RE = re.compile(r"01\.01\.1970")
NUMERIC_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def duck_path(path: Path) -> str:
    return path.resolve().as_posix()


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=4")
    try:
        con.execute("SET memory_limit='6GB'")
    except duckdb.Error:
        pass
    return con


def load_objects(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    src = duck_path(path)
    rows = con.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            COUNT(DISTINCT ид_объект) AS n_ids,
            COUNT(DISTINCT вид_объекта) AS n_kinds
        FROM read_csv('{src}', header=true, auto_detect=true)
        """
    ).fetchone()
    kinds = con.execute(
        f"""
        SELECT вид_объекта, иерархия_уровень, COUNT(*) AS n
        FROM read_csv('{src}', header=true, auto_detect=true)
        GROUP BY 1, 2
        ORDER BY 2, 3 DESC
        """
    ).fetchall()
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "rows": rows[0],
        "unique_ids": rows[1],
        "kind_count": rows[2],
        "by_kind": [
            {"вид_объекта": k, "уровень": int(level), "n": n} for k, level, n in kinds
        ],
    }


def load_channels(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    src = duck_path(path)
    rows = con.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            COUNT(DISTINCT ид_канала_данных) AS n_ids,
            COUNT(DISTINCT ид_объект) AS n_objects,
            COUNT(DISTINCT тип_инж_системы) AS n_systems,
            COUNT(DISTINCT тип_датчика) AS n_types
        FROM read_csv('{src}', header=true, auto_detect=true)
        """
    ).fetchone()
    systems = con.execute(
        f"""
        SELECT тип_инж_системы, COUNT(*) AS n
        FROM read_csv('{src}', header=true, auto_detect=true)
        GROUP BY 1
        ORDER BY n DESC
        """
    ).fetchall()
    types = con.execute(
        f"""
        SELECT тип_датчика, тип_инж_системы, COUNT(*) AS n
        FROM read_csv('{src}', header=true, auto_detect=true)
        GROUP BY 1, 2
        ORDER BY n DESC
        """
    ).fetchall()
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "rows": rows[0],
        "unique_ids": rows[1],
        "objects_with_channels": rows[2],
        "system_count": rows[3],
        "type_count": rows[4],
        "by_system": [{"тип_инж_системы": s, "n": n} for s, n in systems],
        "by_type": [
            {"тип_датчика": t, "тип_инж_системы": s, "n": n} for t, s, n in types
        ],
    }


def journal_view_sql(path: Path) -> str:
    src = duck_path(path)
    return f"""
        SELECT
            TRY_CAST(ид_события AS BIGINT) AS event_id,
            TRY_CAST(ид_канала_данных AS BIGINT) AS channel_id,
            TRY_CAST(дата AS DATE) AS event_date,
            CAST(время AS VARCHAR) AS event_time,
            lower(CAST(тревожное AS VARCHAR)) IN ('t', 'true', '1') AS is_alarm,
            CAST(значение_датчика AS VARCHAR) AS value_raw
        FROM read_csv(
            '{src}',
            header=true,
            auto_detect=true,
            ignore_errors=true,
            sample_size=200000
        )
    """


def classify_value(raw: str | None) -> str:
    if raw is None:
        return "empty"
    text = str(raw).strip()
    if not text:
        return "empty"
    if EPOCH_RE.search(text):
        return "epoch_junk"
    if NUMERIC_RE.match(text.replace(" ", "")):
        return "numeric"
    return "categorical"


def profile_journal(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    channels_path: Path | None = None,
    deep: bool = False,
    sample_values: int = 0,
) -> dict:
    print(f"  профилирую {path.name} ({path.stat().st_size / 1e6:.1f} MB)...", flush=True)
    src_sql = journal_view_sql(path)
    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS n_rows,
            MIN(event_date) AS dmin,
            MAX(event_date) AS dmax,
            COUNT(DISTINCT channel_id) AS n_channels,
            SUM(CAST(is_alarm AS INTEGER)) AS n_alarms
        FROM ({src_sql})
        """
    ).fetchone()
    n_rows, dmin, dmax, n_channels, n_alarms = summary
    n_alarms = int(n_alarms or 0)
    result: dict = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "rows": int(n_rows),
        "date_min": dmin.isoformat() if dmin else None,
        "date_max": dmax.isoformat() if dmax else None,
        "distinct_channels": int(n_channels or 0),
        "alarms": n_alarms,
        "alarm_share": (n_alarms / n_rows) if n_rows else 0.0,
    }
    if not deep:
        return result

    top_alarm = con.execute(
        f"""
        SELECT channel_id, COUNT(*) AS n
        FROM ({src_sql})
        WHERE is_alarm
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 15
        """
    ).fetchall()
    result["top_alarm_channels"] = [{"channel_id": cid, "n": int(n)} for cid, n in top_alarm]

    if channels_path and channels_path.exists():
        ch = duck_path(channels_path)
        orphans = con.execute(
            f"""
            SELECT COUNT(DISTINCT j.channel_id)
            FROM ({src_sql}) j
            LEFT JOIN read_csv('{ch}', header=true, auto_detect=true) c
                ON j.channel_id = TRY_CAST(c.ид_канала_данных AS BIGINT)
            WHERE c.ид_канала_данных IS NULL
            """
        ).fetchone()[0]
        result["orphan_channels"] = int(orphans or 0)

        by_type = con.execute(
            f"""
            SELECT
                COALESCE(c.тип_датчика, 'НЕТ В СПРАВОЧНИКЕ') AS sensor_type,
                COALESCE(c.тип_инж_системы, '—') AS system_type,
                COUNT(*) AS n_events,
                SUM(CAST(j.is_alarm AS INTEGER)) AS n_alarms
            FROM ({src_sql}) j
            LEFT JOIN read_csv('{ch}', header=true, auto_detect=true) c
                ON j.channel_id = TRY_CAST(c.ид_канала_данных AS BIGINT)
            GROUP BY 1, 2
            ORDER BY n_events DESC
            """
        ).fetchall()
        result["events_by_sensor_type"] = [
            {
                "тип_датчика": t,
                "тип_инж_системы": s,
                "events": int(n),
                "alarms": int(a or 0),
            }
            for t, s, n, a in by_type
        ]

        alarm_by_type = [
            row for row in result["events_by_sensor_type"] if row["alarms"] > 0
        ]
        result["alarms_by_sensor_type"] = sorted(
            alarm_by_type, key=lambda r: r["alarms"], reverse=True
        )

    if sample_values:
        values = con.execute(
            f"""
            SELECT value_raw, is_alarm, COUNT(*) AS n
            FROM ({src_sql})
            GROUP BY 1, 2
            ORDER BY n DESC
            LIMIT {int(sample_values)}
            """
        ).fetchall()
        kinds: Counter[str] = Counter()
        catalog: list[dict] = []
        for raw, alarm, n in values:
            kind = classify_value(raw)
            kinds[kind] += int(n)
            catalog.append(
                {
                    "value": raw,
                    "kind": kind,
                    "is_alarm": bool(alarm),
                    "n": int(n),
                }
            )
        result["value_kind_top"] = dict(kinds)
        result["top_values"] = catalog[:40]
    return result


def attach_channel_meta(con: duckdb.DuckDBPyConnection, channels_path: Path, items: list[dict]) -> None:
    if not items:
        return
    src = duck_path(channels_path)
    ids = ", ".join(str(int(x["channel_id"])) for x in items if x.get("channel_id") is not None)
    if not ids:
        return
    rows = con.execute(
        f"""
        SELECT
            ид_канала_данных,
            тип_датчика,
            тип_инж_системы,
            название_датчика,
            ид_объект
        FROM read_csv('{src}', header=true, auto_detect=true)
        WHERE ид_канала_данных IN ({ids})
        """
    ).fetchall()
    by_id = {
        int(cid): {
            "тип_датчика": t,
            "тип_инж_системы": s,
            "название": name,
            "ид_объект": int(obj) if obj is not None else None,
        }
        for cid, t, s, name, obj in rows
    }
    for item in items:
        item.update(by_id.get(int(item["channel_id"]), {}))


def object_orphans(con: duckdb.DuckDBPyConnection, channels_path: Path, objects_path: Path) -> int:
    ch = duck_path(channels_path)
    ob = duck_path(objects_path)
    n = con.execute(
        f"""
        SELECT COUNT(DISTINCT c.ид_объект)
        FROM read_csv('{ch}', header=true, auto_detect=true) c
        LEFT JOIN read_csv('{ob}', header=true, auto_detect=true) o
            ON TRY_CAST(c.ид_объект AS BIGINT) = TRY_CAST(o.ид_объект AS BIGINT)
        WHERE o.ид_объект IS NULL
        """
    ).fetchone()[0]
    return int(n or 0)


def pct(part: float, whole: float) -> str:
    if not whole:
        return "0%"
    return f"{100.0 * part / whole:.3f}%"


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join([line, sep, *body])


def render_markdown(profile: dict) -> str:
    objects = profile["objects"]
    channels = profile["channels"]
    example = profile["example"]
    years = profile["yearly"]

    type_rows = [
        [r["тип_датчика"], r["тип_инж_системы"], r["n"]]
        for r in channels["by_type"]
    ]
    year_rows = [
        [
            y["file"],
            f"{y['bytes'] / 1e9:.2f} ГБ",
            f"{y['rows']:,}".replace(",", " "),
            y["date_min"] or "—",
            y["date_max"] or "—",
            f"{y['alarms']:,}".replace(",", " "),
            pct(y["alarms"], y["rows"]),
            y["distinct_channels"],
        ]
        for y in years
    ]
    example_type_rows = [
        [r["тип_датчика"], r["тип_инж_системы"], r["events"], r["alarms"], pct(r["alarms"], r["events"])]
        for r in example.get("events_by_sensor_type", [])[:20]
    ]
    screamer_rows = [
        [
            s.get("channel_id"),
            s.get("тип_датчика", "—"),
            s.get("название", "—"),
            s.get("n"),
        ]
        for s in example.get("top_alarm_channels", [])
    ]
    value_rows = [
        [v["value"], v["kind"], "да" if v["is_alarm"] else "нет", v["n"]]
        for v in example.get("top_values", [])[:25]
    ]
    kind_rows = [[k, n] for k, n in example.get("value_kind_top", {}).items()]

    total_year_rows = sum(y["rows"] for y in years)
    total_year_alarms = sum(y["alarms"] for y in years)

    return f"""# Профиль данных СМВУ (фаза 1)

Срез на {profile["generated_at"]}. Каталог: `{profile["data_dir"]}`.

## Краткий вывод

- Справочник объектов маленький и иерархический: **{objects["rows"]}** узлов, виды `district` / `controlHouse` / `guardObject`.
- Справочник каналов: **{channels["rows"]}** датчиков, **{channels["type_count"]}** типов, **{channels["system_count"]}** инженерных систем.
- Пример журнала (`{example["file"]}`): **{example["rows"]:,}** событий, **{example["alarms"]}** тревог ({pct(example["alarms"], example["rows"])}), период {example["date_min"]} — {example["date_max"]}.
- Годовые журналы `ext-journal-*.csv`: **{total_year_rows:,}** строк суммарно, **{total_year_alarms:,}** тревог. Это операционный лог СМВУ, не размеченный датасет инцидентов.
- Классы для ML придётся **выводить эвристикой** (статусы `Неисправен` / `Обесточен`, связки дым+тепло, насосы+осадки, одиночные охранные сработки). Честных меток «ложное / выезд / ремонт» нет.

## 1. Инвентарь файлов

{md_table(["файл", "размер", "строки", "дата min", "дата max", "тревоги", "доля тревог", "каналы"], year_rows)}

Дополнительно: пример журнала **{example["rows"]:,}** строк, **{example["bytes"] / 1e6:.1f} МБ**.

## 2. Справочник объектов

- Строк: {objects["rows"]}, уникальных id: {objects["unique_ids"]}.
- Каналы без объекта в справочнике: **{profile["channel_object_orphans"]}**.

{md_table(["вид_объекта", "уровень", "n"], [[r["вид_объекта"], r["уровень"], r["n"]] for r in objects["by_kind"]])}

Имена обезличены (Альфа, Бета, …). Для карты координат нет — только иерархия и пикеты в названиях датчиков.

## 3. Справочник каналов

- Каналов: {channels["rows"]}, объектов с каналами: {channels["objects_with_channels"]}.

Системы:

{md_table(["тип_инж_системы", "n"], [[r["тип_инж_системы"], r["n"]] for r in channels["by_system"]])}

Типы датчиков:

{md_table(["тип_датчика", "тип_инж_системы", "n"], type_rows)}

Карта типов на задачи сервиса:

- **Пожар**: датчик дыма, тепловой, ручной извещатель; газ как фон/утечка.
- **Подтопление**: датчик затопления + состояние насоса; метео снаружи.
- **НСД**: движение, КД дверь/люк/АВ, стекло, охрана. Флаг `тревожное` здесь слабый.
- **Отказ датчика**: `Неисправен`, `Обесточен`, ИБП, дребезг, epoch-мусор `01.01.1970`.
- **Температура**: числовые ряды для фона и аномалий.

## 4. Пример журнала (глубокий разбор)

Файл `{example["file"]}`: {example["date_min"]} — {example["date_max"]}.

- Событий: **{example["rows"]:,}**
- Тревог: **{example["alarms"]}** ({pct(example["alarms"], example["rows"])})
- Уникальных каналов в журнале: {example["distinct_channels"]}
- Каналов журнала нет в справочнике: **{example.get("orphan_channels", "—")}**

События по типу датчика (топ-20):

{md_table(["тип_датчика", "система", "события", "тревоги", "доля тревог"], example_type_rows)}

Топ каналов-«крикунов» (тревоги):

{md_table(["id канала", "тип", "название", "тревог"], screamer_rows)}

## 5. Значения датчиков

В одном поле смешаны числа, статусы и мусор. Классификация топ-значений примера:

{md_table(["вид", "частота в топе"], kind_rows)}

Частые значения:

{md_table(["значение", "вид", "тревога", "n"], value_rows)}

Правила нормализации для фазы 2:

- `t` / `true` / `1` → тревога;
- запятая в числе → точка;
- `01.01.1970…` → NULL (мусор часов/СКУД);
- остальное хранить как категорию (`Норма`, `Неисправен`, `На охране`, `Не замкнут`).

## 6. Что можно предсказывать на этих данных

1. **Отказ / деградация канала** — по потоку `Неисправен`/`Обесточен`, пропускам и дребезгу. Лейбл: статус отказа в горизонте 24ч.
2. **Риск пожара** — совместный рост температуры, тепловых и дымовых сработок на одном пикете. Редкое событие, нужен rule baseline + модель на пикет.
3. **Риск подтопления** — аномальная работа насосов; без метео модель слабая, подключаем Open-Meteo.
4. **НСД vs ложная охрана** — одиночное движение/контакт vs подтверждение соседями и режимом `На охране`/`Снято с охраны`.

Нельзя честно учить «выезд бригады»: такого поля нет. Качество на финале проверяем сравнением прогноза с последующими тревожными паттернами, не с журналом ОДС.

## 7. Дыры и ограничения

- Нет координат / GeoJSON.
- Нет заявок, ППР, решений диспетчера.
- Нет явной разметки ложных тревог.
- Имена объектов обфусцированы.
- Годовые CSV огромные: в Postgres грузить только витрины (тревоги + hourly), сырьё — Parquet.
- Часть каналов журнала отсутствует в справочнике (orphan) — их не выкидывать, помечать `unknown`.

## 8. Целевые метрики (фиксируем на проектировании)

Классы редкие: в примере доля тревог {pct(example["alarms"], example["rows"])}. Accuracy не используем.

| Задача | Precision | Recall | Комментарий |
|---|---|---|---|
| Пожар / подтопление (раннее предупреждение) | ≥ 0.30 | ≥ 0.70 | Лучше лишний осмотр, чем пропуск |
| Отказ датчика | ≥ 0.40 | ≥ 0.60 | Много естественного дребезга |
| Фильтр ложных НСД | ≥ 0.80 | ≥ 0.50 | Нельзя прятать реальный доступ |
| Rule baseline | фиксируем как нижнюю границу |  | LightGBM должен бить правила по PR-AUC |

Сплит по времени: train ≤ 2024, valid 2025, test 2026. Дополнительный смоук — `{example["file"]}`.

Отчётная метрика для экспертизы: **PR-AUC + Precision/Recall на горизонте 24ч**.
"""


def main() -> None:
    if not DATA_DIR.exists():
        raise SystemExit(f"DATA_DIR не найден: {DATA_DIR}")

    objects_path = DATA_DIR / OBJECTS_NAME
    channels_path = DATA_DIR / CHANNELS_NAME
    example_path = DATA_DIR / EXAMPLE_NAME
    yearly_paths = sorted(DATA_DIR.glob("ext-journal-*.csv"))

    print(f"DATA_DIR={DATA_DIR}", flush=True)
    con = connect()

    objects = load_objects(con, objects_path)
    print(f"объекты: {objects['rows']}", flush=True)
    channels = load_channels(con, channels_path)
    print(f"каналы: {channels['rows']}, типов: {channels['type_count']}", flush=True)
    object_orphan_n = object_orphans(con, channels_path, objects_path)

    example = profile_journal(
        con,
        example_path,
        channels_path=channels_path,
        deep=True,
        sample_values=80,
    )
    attach_channel_meta(con, channels_path, example.get("top_alarm_channels", []))

    def persist(yearly: list[dict]) -> dict:
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_dir": str(DATA_DIR),
            "objects": objects,
            "channels": channels,
            "channel_object_orphans": object_orphan_n,
            "example": example,
            "yearly": yearly,
        }
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=json_default),
            encoding="utf-8",
        )
        OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
        return payload

    yearly: list[dict] = []
    persist(yearly)
    print("промежуточный отчёт по справочникам и примеру записан", flush=True)

    for path in yearly_paths:
        deep = path.name.endswith("2026.csv")
        stats = profile_journal(
            con,
            path,
            channels_path=channels_path if deep else None,
            deep=deep,
            sample_values=40 if deep else 0,
        )
        if deep:
            attach_channel_meta(con, channels_path, stats.get("top_alarm_channels", []))
        yearly.append(stats)
        persist(yearly)
        print(f"  готово {path.name}: {stats['rows']} строк, тревог {stats['alarms']}", flush=True)

    persist(yearly)
    print(f"JSON: {OUTPUT_JSON}", flush=True)
    print(f"MD:   {OUTPUT_MD}", flush=True)


if __name__ == "__main__":
    main()
