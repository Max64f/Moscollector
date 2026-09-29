"""SQL построения признаков и эвристических лейблов по журналу СМВУ.

Гранулярность наблюдения: (object_id, hour_ts).
Признаки считаются по окну [hour_ts - 23h, hour_ts], лейблы — по [hour_ts + 1h, hour_ts + 24h].
Такое разделение исключает утечку: событие текущего часа никогда не попадает в лейбл.
"""

from __future__ import annotations

from pathlib import Path

from ml.config import (
    FAILURE_VALUES,
    FAN_TYPE,
    FIRE_PAIRS,
    FLOOD_PAIRS,
    GAS_TYPES,
    GUARD_ON,
    HORIZON_HOURS,
    PUMP_TYPE,
    TEMP_TYPES,
    TEMP_VALID_MAX,
    TEMP_VALID_MIN,
    intrusion_condition,
    pairs_condition,
    types_condition,
    values_condition,
)

# Канал, дающий больше одного "пожара" в сутки в среднем за год, — это
# неисправный датчик, а не пожар. Из лейбла его исключаем, в признаках оставляем.
NOISY_FIRE_EVENTS_PER_YEAR = 365
NOISY_FLOOD_EVENTS_PER_YEAR = 365

MIN_OBJECT_EVENTS_PER_YEAR = 100


def duck_path(path: Path) -> str:
    return path.resolve().as_posix()


def add_seasonal_features(frame):
    """Циклический месяц и сезоны Москвы. Работает на уже собранном parquet (есть `month`)."""
    import numpy as np
    import pandas as pd

    if "month" not in frame.columns:
        return frame
    if "month_sin" in frame.columns:
        return frame
    out = frame.copy()
    month = pd.to_numeric(out["month"], errors="coerce").fillna(0)
    out["month_sin"] = np.sin(2 * np.pi * month / 12.0).astype(np.float32)
    out["month_cos"] = np.cos(2 * np.pi * month / 12.0).astype(np.float32)
    out["flood_season"] = month.isin([3, 4, 5, 6, 7, 8]).astype(np.float32)
    out["heating_season"] = month.isin([11, 12, 1, 2]).astype(np.float32)
    return out


def build_dataset_sql(events_parquet: Path, channels_csv: Path, meteo_parquet: Path | None) -> str:
    """Полный SQL: события -> часовые агрегаты -> окна -> лейблы."""
    events = duck_path(events_parquet)
    channels = duck_path(channels_csv)

    fire_cond = pairs_condition(FIRE_PAIRS)
    flood_cond = pairs_condition(FLOOD_PAIRS)
    failure_cond = values_condition(FAILURE_VALUES)
    intrusion_cond = intrusion_condition()
    temp_cond = types_condition(TEMP_TYPES)
    gas_cond = types_condition(GAS_TYPES)

    meteo_join = "LEFT JOIN (SELECT NULL::DATE AS day, NULL::DOUBLE AS temp_out, NULL::DOUBLE AS precip, NULL::DOUBLE AS humidity) m ON FALSE"
    if meteo_parquet is not None and meteo_parquet.exists():
        meteo_join = f"""
        LEFT JOIN (
            SELECT day, temp_mean AS temp_out, precipitation_mm AS precip, humidity
            FROM read_parquet('{duck_path(meteo_parquet)}')
        ) m ON m.day = CAST(g.hour_ts AS DATE)
        """

    return f"""
    WITH channels AS (
        SELECT
            TRY_CAST(ид_канала_данных AS BIGINT) AS channel_id,
            тип_датчика AS sensor_type,
            тип_инж_системы AS system_type,
            TRY_CAST(ид_объект AS BIGINT) AS object_id,
            regexp_extract(название_датчика, 'ПК\\s*(\\d+)', 1) AS picket
        FROM read_csv('{channels}', header=true, auto_detect=true)
    ),
    -- Статичный портрет объекта: какие подсистемы вообще есть.
    object_caps AS (
        SELECT
            object_id,
            COUNT(*) AS n_channels,
            COUNT(*) FILTER (WHERE sensor_type = 'Датчик дыма') AS n_smoke,
            COUNT(*) FILTER (WHERE sensor_type = 'Тепловой датчик') AS n_heat,
            COUNT(*) FILTER (WHERE sensor_type = '{PUMP_TYPE}') AS n_pump,
            COUNT(*) FILTER (WHERE sensor_type = 'Датчик затопления') AS n_flood_sensor,
            COUNT(*) FILTER (WHERE sensor_type = 'Датчик температуры') AS n_temp,
            COUNT(*) FILTER (WHERE sensor_type = 'Газовый датчик') AS n_gas,
            COUNT(*) FILTER (WHERE sensor_type IN ('Датчик движения', 'КД Дверь', 'КД АВ', 'КД Люк')) AS n_guard,
            COUNT(DISTINCT picket) FILTER (WHERE picket <> '') AS n_pickets
        FROM channels
        WHERE object_id IS NOT NULL
        GROUP BY 1
    ),
    ev AS (
        SELECT
            e.event_ts,
            e.channel_id,
            e.is_alarm,
            e.value_num,
            e.value_cat,
            c.object_id,
            c.sensor_type,
            c.picket
        FROM read_parquet('{events}') e
        JOIN channels c ON c.channel_id = e.channel_id
        WHERE e.event_ts IS NOT NULL
          AND c.object_id IS NOT NULL
    ),
    flagged AS (
        SELECT
            object_id,
            channel_id,
            picket,
            sensor_type,
            is_alarm,
            date_trunc('hour', event_ts) AS hour_ts,
            ({fire_cond}) AS ev_fire,
            ({flood_cond}) AS ev_flood,
            (
                ({failure_cond})
                OR (({temp_cond}) AND value_num IS NOT NULL
                    AND (value_num < {TEMP_VALID_MIN} OR value_num > {TEMP_VALID_MAX}))
            ) AS ev_failure,
            ({intrusion_cond}) AS ev_intrusion,
            CASE WHEN ({temp_cond}) AND value_num BETWEEN {TEMP_VALID_MIN} AND {TEMP_VALID_MAX}
                 THEN value_num END AS temp_val,
            CASE WHEN ({gas_cond}) THEN value_num END AS gas_val,
            (sensor_type = '{PUMP_TYPE}' AND value_cat = 'Включен') AS pump_on,
            (sensor_type = '{FAN_TYPE}' AND value_cat = 'Включен') AS fan_on,
            (sensor_type = 'Состояние охраны' AND value_cat = '{GUARD_ON}') AS guard_set
        FROM ev
    ),
    -- Каналы-крикуны: неисправные датчики, которые генерируют "пожар"/"воду" пачками.
    noisy AS (
        SELECT
            channel_id,
            SUM(CASE WHEN ev_fire THEN 1 ELSE 0 END) > {NOISY_FIRE_EVENTS_PER_YEAR} AS noisy_fire,
            SUM(CASE WHEN ev_flood THEN 1 ELSE 0 END) > {NOISY_FLOOD_EVENTS_PER_YEAR} AS noisy_flood
        FROM flagged
        GROUP BY 1
    ),
    -- Отказ интересен только новый. Датчик, который "Неисправен" месяцами,
    -- не нуждается в прогнозе, поэтому смотрим на канал и его последние сутки.
    chan_hourly AS (
        SELECT
            object_id,
            channel_id,
            hour_ts,
            SUM(CASE WHEN ev_failure THEN 1 ELSE 0 END) AS n_failure
        FROM flagged
        GROUP BY 1, 2, 3
    ),
    chan_marked AS (
        SELECT
            object_id,
            hour_ts,
            n_failure,
            COALESCE(
                SUM(n_failure) OVER (
                    PARTITION BY channel_id ORDER BY hour_ts
                    RANGE BETWEEN INTERVAL 24 HOUR PRECEDING
                              AND INTERVAL 1 HOUR PRECEDING
                ), 0
            ) AS prev_failure
        FROM chan_hourly
    ),
    obj_new_failure AS (
        SELECT
            object_id,
            hour_ts,
            SUM(CASE WHEN n_failure > 0 AND prev_failure = 0 THEN 1 ELSE 0 END)::INTEGER
                AS n_failure_new
        FROM chan_marked
        GROUP BY 1, 2
    ),
    hourly AS (
        SELECT
            f.object_id,
            f.hour_ts,
            COUNT(*)::INTEGER AS n_events,
            COUNT(DISTINCT f.channel_id)::INTEGER AS n_channels_active,
            SUM(CASE WHEN f.is_alarm THEN 1 ELSE 0 END)::INTEGER AS n_alarms,
            SUM(CASE WHEN f.ev_fire THEN 1 ELSE 0 END)::INTEGER AS n_fire,
            SUM(CASE WHEN f.ev_flood THEN 1 ELSE 0 END)::INTEGER AS n_flood,
            SUM(CASE WHEN f.ev_failure THEN 1 ELSE 0 END)::INTEGER AS n_failure,
            SUM(CASE WHEN f.ev_intrusion THEN 1 ELSE 0 END)::INTEGER AS n_intrusion,
            -- Лейбловые счётчики: без каналов-крикунов.
            SUM(CASE WHEN f.ev_fire AND NOT COALESCE(n.noisy_fire, FALSE) THEN 1 ELSE 0 END)::INTEGER AS n_fire_clean,
            SUM(CASE WHEN f.ev_flood AND NOT COALESCE(n.noisy_flood, FALSE) THEN 1 ELSE 0 END)::INTEGER AS n_flood_clean,
            COUNT(DISTINCT CASE WHEN f.ev_fire THEN f.picket END)::INTEGER AS n_fire_pickets,
            COUNT(DISTINCT CASE WHEN f.ev_intrusion THEN f.channel_id END)::INTEGER AS n_intrusion_channels,
            SUM(CASE WHEN f.pump_on THEN 1 ELSE 0 END)::INTEGER AS n_pump_on,
            SUM(CASE WHEN f.fan_on THEN 1 ELSE 0 END)::INTEGER AS n_fan_on,
            AVG(f.temp_val) AS temp_mean,
            MAX(f.temp_val) AS temp_max,
            MIN(f.temp_val) AS temp_min,
            AVG(f.gas_val) AS gas_mean,
            MAX(f.gas_val) AS gas_max,
            MAX(CASE WHEN f.sensor_type = 'Состояние охраны'
                     THEN CASE WHEN f.guard_set THEN 1 ELSE 0 END END) AS guard_state,
            MAX(cnt.per_channel)::INTEGER AS max_events_per_channel,
            MAX(COALESCE(nf.n_failure_new, 0))::INTEGER AS n_failure_new
        FROM flagged f
        LEFT JOIN noisy n ON n.channel_id = f.channel_id
        LEFT JOIN obj_new_failure nf
            ON nf.object_id = f.object_id AND nf.hour_ts = f.hour_ts
        LEFT JOIN (
            SELECT object_id, hour_ts, channel_id, COUNT(*) AS per_channel
            FROM flagged
            GROUP BY 1, 2, 3
        ) cnt ON cnt.object_id = f.object_id AND cnt.hour_ts = f.hour_ts AND cnt.channel_id = f.channel_id
        GROUP BY 1, 2
    ),
    -- Объекты, которые в этом периоде реально передавали данные.
    live_objects AS (
        SELECT object_id
        FROM hourly
        GROUP BY 1
        HAVING SUM(n_events) >= {MIN_OBJECT_EVENTS_PER_YEAR}
    ),
    bounds AS (
        SELECT MIN(hour_ts) AS ts_min, MAX(hour_ts) AS ts_max FROM hourly
    ),
    grid AS (
        SELECT o.object_id, gs.hour_ts
        FROM live_objects o
        CROSS JOIN (
            SELECT UNNEST(generate_series(
                (SELECT ts_min FROM bounds),
                (SELECT ts_max FROM bounds),
                INTERVAL 1 HOUR
            )) AS hour_ts
        ) gs
    ),
    joined AS (
        SELECT
            g.object_id,
            g.hour_ts,
            COALESCE(h.n_events, 0) AS n_events,
            COALESCE(h.n_channels_active, 0) AS n_channels_active,
            COALESCE(h.n_alarms, 0) AS n_alarms,
            COALESCE(h.n_fire, 0) AS n_fire,
            COALESCE(h.n_flood, 0) AS n_flood,
            COALESCE(h.n_failure, 0) AS n_failure,
            COALESCE(h.n_failure_new, 0) AS n_failure_new,
            COALESCE(h.n_intrusion, 0) AS n_intrusion,
            COALESCE(h.n_fire_clean, 0) AS n_fire_clean,
            COALESCE(h.n_flood_clean, 0) AS n_flood_clean,
            COALESCE(h.n_fire_pickets, 0) AS n_fire_pickets,
            COALESCE(h.n_intrusion_channels, 0) AS n_intrusion_channels,
            COALESCE(h.n_pump_on, 0) AS n_pump_on,
            COALESCE(h.n_fan_on, 0) AS n_fan_on,
            COALESCE(h.max_events_per_channel, 0) AS max_events_per_channel,
            h.temp_mean,
            h.temp_max,
            h.temp_min,
            h.gas_mean,
            h.gas_max,
            h.guard_state,
            m.temp_out,
            m.precip,
            m.humidity
        FROM grid g
        LEFT JOIN hourly h ON h.object_id = g.object_id AND h.hour_ts = g.hour_ts
        {meteo_join}
    ),
    rolled AS (
        SELECT
            object_id,
            hour_ts,
            n_events AS ev_1h,
            n_alarms AS al_1h,
            n_fire AS fire_1h,
            n_flood AS flood_1h,
            n_failure AS failure_1h,
            n_intrusion AS intrusion_1h,
            temp_out,
            precip,
            humidity,
            guard_state,
            SUM(n_events) OVER w6 AS ev_6h,
            SUM(n_events) OVER w24 AS ev_24h,
            SUM(n_alarms) OVER w6 AS al_6h,
            SUM(n_alarms) OVER w24 AS al_24h,
            SUM(n_fire) OVER w6 AS fire_6h,
            SUM(n_fire) OVER w24 AS fire_24h,
            SUM(n_flood) OVER w6 AS flood_6h,
            SUM(n_flood) OVER w24 AS flood_24h,
            SUM(n_failure) OVER w6 AS failure_6h,
            SUM(n_failure) OVER w24 AS failure_24h,
            SUM(n_failure_new) OVER w24 AS failure_new_24h,
            SUM(n_intrusion) OVER w6 AS intrusion_6h,
            SUM(n_intrusion) OVER w24 AS intrusion_24h,
            MAX(n_fire_pickets) OVER w6 AS fire_pickets_6h,
            MAX(n_intrusion_channels) OVER w6 AS intrusion_channels_6h,
            SUM(n_pump_on) OVER w6 AS pump_on_6h,
            SUM(n_pump_on) OVER w24 AS pump_on_24h,
            SUM(n_fan_on) OVER w24 AS fan_on_24h,
            MAX(max_events_per_channel) OVER w24 AS chatter_max_24h,
            SUM(n_channels_active) OVER w24 AS channel_touches_24h,
            AVG(temp_mean) OVER w6 AS temp_mean_6h,
            AVG(temp_mean) OVER w24 AS temp_mean_24h,
            MAX(temp_max) OVER w6 AS temp_max_6h,
            MAX(temp_max) OVER w24 AS temp_max_24h,
            MIN(temp_min) OVER w24 AS temp_min_24h,
            AVG(gas_mean) OVER w24 AS gas_mean_24h,
            MAX(gas_max) OVER w6 AS gas_max_6h,
            MAX(gas_max) OVER w24 AS gas_max_24h,
            -- Последний известный режим охраны.
            last_value(guard_state IGNORE NULLS) OVER (
                PARTITION BY object_id ORDER BY hour_ts
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS guard_last,
            -- Лейблы: только будущее, начиная со следующего часа.
            SUM(n_fire_clean) OVER wf AS y_fire_cnt,
            SUM(n_flood_clean) OVER wf AS y_flood_cnt,
            SUM(n_failure_new) OVER wf AS y_failure_cnt,
            SUM(n_intrusion) OVER wf AS y_intrusion_cnt
        FROM joined
        WINDOW
            w6 AS (
                PARTITION BY object_id ORDER BY hour_ts
                RANGE BETWEEN INTERVAL 5 HOUR PRECEDING AND CURRENT ROW
            ),
            w24 AS (
                PARTITION BY object_id ORDER BY hour_ts
                RANGE BETWEEN INTERVAL 23 HOUR PRECEDING AND CURRENT ROW
            ),
            wf AS (
                PARTITION BY object_id ORDER BY hour_ts
                RANGE BETWEEN INTERVAL 1 HOUR FOLLOWING AND INTERVAL {HORIZON_HOURS} HOUR FOLLOWING
            )
    )
    SELECT
        r.object_id,
        r.hour_ts,
        CAST(r.hour_ts AS DATE) AS day,
        EXTRACT(hour FROM r.hour_ts)::INTEGER AS hour_of_day,
        EXTRACT(dow FROM r.hour_ts)::INTEGER AS day_of_week,
        EXTRACT(month FROM r.hour_ts)::INTEGER AS month,
        SIN(2 * pi() * EXTRACT(month FROM r.hour_ts) / 12.0) AS month_sin,
        COS(2 * pi() * EXTRACT(month FROM r.hour_ts) / 12.0) AS month_cos,
        CASE WHEN EXTRACT(month FROM r.hour_ts) IN (3, 4, 5, 6, 7, 8) THEN 1 ELSE 0 END AS flood_season,
        CASE WHEN EXTRACT(month FROM r.hour_ts) IN (11, 12, 1, 2) THEN 1 ELSE 0 END AS heating_season,
        caps.n_channels,
        caps.n_smoke,
        caps.n_heat,
        caps.n_pump,
        caps.n_flood_sensor,
        caps.n_temp,
        caps.n_gas,
        caps.n_guard,
        caps.n_pickets,
        r.ev_1h, r.ev_6h, r.ev_24h,
        r.al_1h, r.al_6h, r.al_24h,
        r.fire_1h, r.fire_6h, r.fire_24h,
        r.flood_1h, r.flood_6h, r.flood_24h,
        r.failure_1h, r.failure_6h, r.failure_24h, r.failure_new_24h,
        r.intrusion_1h, r.intrusion_6h, r.intrusion_24h,
        r.fire_pickets_6h,
        r.intrusion_channels_6h,
        r.pump_on_6h, r.pump_on_24h, r.fan_on_24h,
        r.chatter_max_24h,
        r.channel_touches_24h,
        CASE WHEN r.channel_touches_24h > 0
             THEN r.ev_24h::DOUBLE / r.channel_touches_24h END AS events_per_channel_24h,
        r.temp_mean_6h, r.temp_mean_24h,
        r.temp_max_6h, r.temp_max_24h, r.temp_min_24h,
        CASE WHEN r.temp_mean_6h IS NOT NULL AND r.temp_mean_24h IS NOT NULL
             THEN r.temp_mean_6h - r.temp_mean_24h END AS temp_trend,
        r.gas_mean_24h, r.gas_max_6h, r.gas_max_24h,
        COALESCE(r.guard_last, 0) AS guard_on,
        r.temp_out, r.precip, r.humidity,
        COALESCE(r.y_fire_cnt, 0)::INTEGER AS y_fire_cnt,
        COALESCE(r.y_flood_cnt, 0)::INTEGER AS y_flood_cnt,
        COALESCE(r.y_failure_cnt, 0)::INTEGER AS y_failure_cnt,
        COALESCE(r.y_intrusion_cnt, 0)::INTEGER AS y_intrusion_cnt,
        CASE WHEN COALESCE(r.y_fire_cnt, 0) > 0 THEN 1 ELSE 0 END AS y_fire,
        CASE WHEN COALESCE(r.y_flood_cnt, 0) > 0 THEN 1 ELSE 0 END AS y_flood,
        CASE WHEN COALESCE(r.y_failure_cnt, 0) > 0 THEN 1 ELSE 0 END AS y_failure,
        CASE WHEN COALESCE(r.y_intrusion_cnt, 0) > 0 THEN 1 ELSE 0 END AS y_intrusion
    FROM rolled r
    JOIN object_caps caps ON caps.object_id = r.object_id
    -- Последние сутки периода отбрасываем: горизонт лейбла выходит за границу файла.
    WHERE r.hour_ts <= (SELECT MAX(hour_ts) FROM rolled) - INTERVAL {HORIZON_HOURS} HOUR
    ORDER BY r.object_id, r.hour_ts
    """
