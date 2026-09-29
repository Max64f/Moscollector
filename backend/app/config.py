from datetime import datetime
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    parquet_dir: Path = Path("/parquet")
    models_dir: Path = Path("/parquet/models")
    database_url: str = "postgresql+psycopg://ldt:ldt@db:5432/moscollector"
    cors_origins: str = "http://localhost:5173"


settings = Settings()

OBJECTS_CSV = "справочник_объектов_диспетчер.csv"
CHANNELS_CSV = "справочник_каналов_датчиков.csv"
EXAMPLE_CSV = "журнал_событий_пример.csv"

EPOCH_MARK = "01.01.1970"
HOURLY_SENSOR_TYPES = (
    "Датчик температуры",
    "Тепловой датчик",
    "Датчик затопления",
    "Состояние насоса",
    "Состояние вентилятора",
)


def parse_alarm(value: object) -> bool:
    return str(value).strip().lower() in {"t", "true", "1", "yes"}


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or EPOCH_MARK in text:
        return None
    normalized = text.replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_category(value: object, number: float | None) -> str | None:
    if value is None or number is not None:
        return None
    text = str(value).strip()
    if not text or EPOCH_MARK in text:
        return None
    return text


def parse_timestamp(date_value: object, time_value: object) -> datetime | None:
    date_text = str(date_value).strip().strip('"')
    time_text = str(time_value).strip().strip('"')
    if not date_text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            if fmt.endswith("%S"):
                return datetime.strptime(f"{date_text} {time_text}", fmt)
            return datetime.strptime(date_text, fmt)
        except ValueError:
            continue
    return None
