# Москоллектор

Сервис для диспетчера ОДС: прогноз отказа, пожара, подтопления и ложных срабатываний.
Стек обычный: Postgres, FastAPI, nginx со статикой. Модели — LightGBM.

Сырых журналов в репозитории нет. Их дают отдельно, в git они не нужны.

## Запуск

Нужен Docker Desktop.

Скопируйте `.env.example` в `.env`. В `.env` поправьте `DATA_DIR`.
По умолчанию это `../dataset` относительно корня репозитория.
Там должны лежать CSV справочников и журналов СМВУ. `PARQUET_DIR` можно оставить рядом, в `../dataset/parquet`.

```bash
docker compose up --build
```

После старта:

- UI: http://localhost:5173
- API: http://localhost:8000/health
- Swagger: http://localhost:8000/docs
- Postgres: localhost:5433, пользователь `ldt`, пароль `ldt`, база `moscollector`

Остановка: `docker compose down`. Том базы живёт отдельно, `down -v` снесёт и его.

## Данные

Пустой дашборд — нормально, в базе ещё ничего нет. Справочники и пример журнала:

```bash
curl -X POST "http://localhost:8000/import/bootstrap"
```

Полный склад из CSV (если есть годы, не только пример):

```bash
docker compose --profile etl run --rm etl python to_parquet.py
docker compose --profile etl run --rm etl python load_warehouse.py
```

Погода, признаки, обучение, запись прогнозов в Postgres:

```bash
docker compose --profile etl run --rm etl python meteo.py
docker compose --profile ml run --rm ml python -m ml.build_dataset
docker compose --profile ml run --rm ml python -m ml.train
docker compose --profile ml run --rm ml python -m ml.predict
```

Потоки LightGBM: `LGBM_THREADS` в `.env`. Если модели уже лежат в `PARQUET_DIR/models`, train можно не гонять.

## Роли

LDAP нет, не ищите. Заголовок `X-User-Login`: `dispatcher`, `analyst`, `manager`.
В UI то же самое переключается в шапке. По умолчанию диспетчер.

## UI без Docker

Если очень хочется Node локально, а не nginx из compose:

```bash
cd frontend
npm install
npm run dev
```

В Docker фронт отдаётся из `frontend/docker`. Сборка npm в образе специально не используется.

## Что где

- `backend/` — API и миграции
- `frontend/` — дашборд
- `etl/` — CSV → Parquet и витрины
- `ml/` — признаки и модели
- `docker-compose.yml` — всё остальное
