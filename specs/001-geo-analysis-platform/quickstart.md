# Quickstart: проверка фичи «Базовая платформа геопространственного анализа»

**Feature**: `001-geo-analysis-platform` | **Date**: 2026-09-26 | **Plan**: [plan.md](./plan.md) | **Data model**: [data-model.md](./data-model.md) | **Contracts**: [contracts/](./contracts/)

Документ описывает **как запустить и проверить** фичу end-to-end. Детали схемы — в `data-model.md`,
форматы запросов и ответов — в `contracts/`, реализация — в `tasks.md` и в коде.

## 1. Предварительные требования

- Docker Engine + Docker Compose v2 (Linux/macOS или WSL2 на Windows).
- Python 3.11+ и `uv` для локальных прогонов тестов.
- Flutter SDK (канал stable) с поддержкой Web.
- Свободный доступ в интернет к STAC-каталогам (Earth Search, Planetary Computer) и Nominatim/OSM.
- **Регистрация не требуется**: все источники данных и геокодирование используются анонимно (research R1, R5).
  Ключи Copernicus Data Space и OpenAI — опциональны и задаются только переменными окружения.

## 2. Настройка окружения

```bash
# 1. Переменные окружения (секреты — только здесь, в коде их быть не должно)
cp .env.example .env

# 2. Поднять инфраструктуру: postgres+postgis+pgvector, redis, langfuse, api, worker
docker compose up -d

# 3. Схема БД (расширения postgis и pgvector создаются первой ревизией)
uv run alembic upgrade head

# 4. Проиндексировать доменные знания для RAG (нормы, формулы, история)
uv run python -m glist.rag.indexer --source ./knowledge --version v1

# 5. Собрать и запустить веб-клиент
cd frontend && flutter pub get && flutter run -d chrome
```

Обязательные переменные окружения: `DATABASE_URL`, `REDIS_URL`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LLM_PROVIDER` (`openai` | `ollama`), `LLM_MODEL`, `EMBEDDING_MODEL`, `SEED`.
Для полностью локального режима: `LLM_PROVIDER=ollama` и `OLLAMA_BASE_URL=http://localhost:11434`.

## 3. Проверочные сценарии

### V1. Типовой запрос: карта, объяснение, источники, экспорт (US1, FR-001…FR-009, SC-001, SC-004)

```bash
curl -s -X POST http://localhost:8000/api/v1/queries \
  -H 'Content-Type: application/json' \
  -d '{"text":"Покажи, как изменилась зелёная зона в Центральном районе за последние 5 лет"}'
```

**Ожидаемо**: `201` с `run.id` и статусом `ожидает` или `в обработке`; полный результат — не позднее
60 секунд (SC-001). Далее:

```bash
curl -s http://localhost:8000/api/v1/runs/{runId}/result      # территории, 3 класса, объяснение, источники
curl -s -X POST http://localhost:8000/api/v1/runs/{runId}/exports -d '{"format":"geojson"}'
curl -s -X POST http://localhost:8000/api/v1/runs/{runId}/exports -d '{"format":"pdf"}'
```

**Критерии приёмки**: в ответе присутствуют классы `retained`/`lost`/`new` с площадями; объяснение
содержит даты съёмки и источники; GeoJSON валиден по [contracts/geojson-export.schema.json](./contracts/geojson-export.schema.json)
и содержит `crs`; PDF содержит карту, объяснение, источники и таблицы по территориям (FR-014).

### V2. История и открытие результата без повторного анализа (US2, FR-013, SC-010)

```bash
curl -s 'http://localhost:8000/api/v1/queries?limit=20'
curl -s http://localhost:8000/api/v1/queries/{queryId}        # цепочка запусков (parent_run_id)
```

**Ожидаемо**: все прошлые запросы видны со статусами; открытие результата занимает ≤5 секунд и не
создаёт нового запуска (SC-010).

### V3. Уточняющий запрос «дополнить» (US3, FR-011, FR-012, SC-009)

```bash
curl -s -X POST http://localhost:8000/api/v1/queries \
  -H 'Content-Type: application/json' \
  -d '{"text":"А что насчёт парка рядом с метро?","parentQueryId":"{queryId}","mode":"add"}'
```

**Ожидаемо**: период и тип анализа унаследованы; комбинированный результат содержит **все** ранее
накопленные территории (не только последнюю) плюс новую; слои хранятся раздельно; суммарная статистика
помечена «сумма по территориям (с возможным пересечением)»; при попытке добавить шестую территорию —
отказ с предложением создать новый запрос (лимит 5).

### V4. Live-прогресс и очередь (US4, FR-010, FR-021, SC-007, SC-012)

```bash
# Подписка на события прогресса запуска
websocat ws://localhost:8000/ws/runs/{runId}
# или в браузере DevTools для Flutter Web
curl -s http://localhost:8000/api/v1/queue
```

**Ожидаемо**: первое событие (`run.queued` / `run.stage.started`) приходит не позднее 5 секунд;
обновления — не реже раза в 5 секунд; каждый этап содержит непустое описание работы агента; при
отправке второго запроса во время обработки первого он виден в очереди и стартует автоматически;
активный запуск всегда ровно один.

### V5. Отсутствие пригодных данных и альтернативы (US5, FR-015, SC-008)

Запрос по территории/периоду без пригодных сцен (например, период с облачностью выше 20 % на всех сценах).

**Ожидаемо**: статус `данные недоступны` с человекочитаемой причиной; в ответе и в UI — не менее трёх
альтернатив (другой период, другой источник, ближайшая доступная дата); выбор альтернативы через
`POST /api/v1/clarifications/{id}/answer` продолжает обработку **без повторного ввода** исходного запроса.

### V6. Отказ валидатора и правило одной переработки (FR-007, SC-011)

Прогон с мок-провайдером, принудительно возвращающим отклонение:

```bash
uv run pytest tests/integration -k validator -q
```

**Ожидаемо**:

- `analysis_error` → ровно одна переработка, затем результат со статусом `не прошёл проверку`
  (если снова отклонён) и **обеими** группами причин: первое отклонение, `fixesApplied` второй
  итерации, второе отклонение;
- `data_quality` → переработка не запускается, статус `не прошёл проверку` выставляется сразу;
- результат в статусе `не прошёл проверку` доступен для просмотра и экспорта так же, как `завершён`;
- в `GET /runs/{id}` статусы различимы: `завершён` ≠ `не прошёл проверку` ≠ `ошибка`.

### V7. Трассировка по идентификатору запуска (FR-016, SC-005)

```bash
curl -s http://localhost:8000/api/v1/runs/{runId}/trace
```

**Ожидаемо**: возвращён `traceId`; в интерфейсе LangFuse по нему восстанавливается полная
последовательность: узлы графа, вызовы инструментов (STAC, NDVI, PostGIS), вызовы LLM (модель,
параметры, токены) и причины отклонений валидатора. Трассировка обязательна: при недоступности
LangFuse запуск завершается статусом `ошибка`, а не продолжается молча.

### V8. Геопространственная корректность (FR-017, Принцип IV конституции)

```bash
docker compose exec postgres psql -U glist -c \
  "select ST_SRID(boundary) srid, ST_IsValid(boundary) valid, count(*) from area_of_interest group by 1,2;"
docker compose exec postgres psql -U glist -c \
  "select ST_SRID(geom) srid, ST_IsValid(geom) valid, count(*) from change_zone group by 1,2;"
```

**Ожидаемо**: ровно один SRID = 4326 и `valid = t` во всех строках; экспортированный GeoJSON содержит
`crs` CRS84; площади в отчёте совпадают с `ST_Area` по данным PostGIS.

### V9. Политика хранения и очистки (FR-022, FR-023, SC-013)

```bash
uv run python -m glist.jobs.retention --now-shift '13 months'     # тестовый сдвиг времени
docker compose exec postgres psql -U glist -c \
  "select kind, is_heavy, retention_until, is_deleted from artifact where run_id = '{runId}';"
docker compose exec postgres psql -U glist -c "select * from artifact_deletion_log order by deleted_at desc limit 10;"
```

**Ожидаемо**: удаляются только `raw_scene`, `intermediate_ndvi`, `intermediate_classified` — при
истечении 12 месяцев и только если артефакт «тяжёлый» (> 100 МБ); превью PNG, финальный GeoTIFF,
GeoJSON, PDF и метаданные сохраняются бессрочно; запуски с `is_reference` или `is_pinned` не
очищаются никогда; `PUT /runs/{id}/pin` снимает плановое удаление; каждое удаление записано в журнал;
уведомление (`purge_notice_at`) выставляется за 7 дней до плановой очистки и видно в истории.

### V10. Идемпотентность повторного запроса (FR-019, R16)

Повторно отправить тот же запрос с теми же параметрами.

**Ожидаемо**: создаётся **новый** запуск с другим `id` и заполненным `reusedResultId`; повторный
тяжёлый анализ не выполняется; дубликат результата не создаётся; оба запуска видны в истории и
различимы по идентификатору.

## 4. Приёмочный прогон точности (SC-002)

```bash
uv run pytest tests/acceptance -k sc002 -q
```

**Ожидаемо**: на контрольном наборе (2 контрольные территории, не менее 50 проверочных участков на
каждой; разметка создаётся вручную с опорой на ESA WorldCover) одновременно выполняются **общая
точность ≥ 85 %** и **F1 ≥ 70 %** по каждому из трёх классов; отчёт содержит матрицу ошибок.

## 5. Гейты качества (обязательны перед слиянием)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src/
uv run pytest --cov=src/glist/tools --cov=src/glist/services --cov-fail-under=80
uv run alembic check
cd frontend && dart format --output=none --set-exit-if-changed . && flutter analyze
```

**Ожидаемо**: покрытие ≥ 80 % по инструментам и бизнес-логике (пакеты `agents/` исключены из метрики —
они покрываются интеграционными тестами графа); линтеры, строгая типизация, миграции и анализ Flutter —
без ошибок и предупреждений.

## 6. Итоговая таблица ожиданий

| Проверка | Критерий | Источник |
|---|---|---|
| V1 | Полный результат ≤60 с в 9 из 10 запусков; источники и даты съёмки на месте | SC-001, SC-004 |
| V2 | Открытие из истории ≤5 с без повторного анализа | SC-010 |
| V3 | Учёт всех накопленных территорий; лимит 5; пометка о пересечении | SC-009, FR-012 |
| V4 | Первый этап ≤5 с, обновления ≤5 с; ровно один активный запуск | SC-007, SC-012 |
| V5 | Причина и не менее трёх альтернатив без повторного ввода | SC-008 |
| V6 | Ровно одна переработка; отдельный статус «не прошёл проверку» | FR-007, SC-011 |
| V7 | Полная трассировка по идентификатору запуска | SC-005 |
| V8 | EPSG:4326 и валидность геометрий везде | FR-017, Принцип IV |
| V9 | Частичная очистка по видам артефактов; журнал; уведомление за 7 дней | FR-022, FR-023, SC-013 |
| V10 | Повторный запуск без дубликата результата | FR-019 |
| Точность | Общая точность ≥85 % и F1 ≥70 % по каждому классу | SC-002 |

## 7. Ссылки

- Спецификация: [spec.md](./spec.md) · План: [plan.md](./plan.md) · Исследование: [research.md](./research.md)
- Модель данных: [data-model.md](./data-model.md)
- Контракты: [api.yaml](./contracts/api.yaml) · [websocket-events.md](./contracts/websocket-events.md) ·
  [geojson-export.schema.json](./contracts/geojson-export.schema.json) · [agent-state.md](./contracts/agent-state.md)
- Конституция проекта: [.specify/memory/constitution.md](../../.specify/memory/constitution.md)