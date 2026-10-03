# Tasks: Базовая платформа геопространственного анализа

**Input**: Design documents from `/specs/001-geo-analysis-platform/`

**Prerequisites**: [plan.md](./plan.md) (стек, структура), [spec.md](./spec.md) (5 историй, P1–P5), [research.md](./research.md) (решения R1–R16), [data-model.md](./data-model.md) (18 сущностей), [contracts/](./contracts/) (api.yaml, websocket-events.md, geojson-export.schema.json, agent-state.md), [quickstart.md](./quickstart.md) (сценарии V1–V10)

**Tests**: тестовые задачи **включены** — этого требует конституция проекта (Принцип VIII, NON-NEGOTIABLE): покрытие ≥80% по инструментам и бизнес-логике, обязательные unit-тесты инструментов и интеграционные тесты графа агентов, приёмочный контур SC-002. Это не TDD из спецификации, а обязательные блокирующие гейты CI (`.specify/memory/constitution.md` → «Процесс разработки и контроль качества»).

**Constitution Check**: ограничения стека зафиксированы в [plan.md](./plan.md) → «Constitution Check» и «Complexity Tracking» (4 обоснованных отклонения, включая отложенную JWT-аутентификацию). Новые отклонения без амендмента конституции вводить нельзя.

**Organization**: задачи сгруппированы по пользовательским историям — каждая реализуется и тестируется независимо (MVP = US1).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно выполнять параллельно (другие файлы, нет зависимостей от незавершённых задач)
- **[Story]**: принадлежность пользовательской истории (US1…US5)
- В описании указан точный путь к файлу

## Path Conventions

Структура «web application» из [plan.md](./plan.md):

- **Backend**: `backend/src/glist/…`; тесты — `backend/tests/{unit,integration,contract,acceptance,fixtures}/`
- **Frontend**: `frontend/lib/…`; тесты — `frontend/test/`
- **Инфраструктура**: `docker-compose.yml`, `.github/workflows/ci.yml`, `.env.example`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: инициализация репозитория, зависимостей и локального стенда

- [ ] T001 Создать структуру пакетов и тестовых каталогов из [plan.md](./plan.md) → «Source Code»: `backend/src/glist/{api,agents,llm,tools,rag,db,services,jobs,trace}/__init__.py` и `backend/tests/{unit,integration,contract,acceptance,fixtures}/__init__.py`
- [ ] T002 Создать манифест зависимостей и конфигурацию инструментов в `backend/pyproject.toml`: рантайм — fastapi, uvicorn, sqlmodel, alembic, pydantic v2, pydantic-settings, langgraph, langfuse, rasterio, geopandas, numpy, httpx, pystac-client, arq, redis, reportlab, matplotlib; dev — pytest, pytest-asyncio, pytest-cov, testcontainers, ruff, mypy, scikit-learn; секции `[tool.ruff]` (line-length 100), `[tool.mypy]` (`strict = true`), `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`), `[tool.coverage.report]` с `omit = ["*/agents/*"]`; зафиксировать `backend/uv.lock`
- [ ] T003 [P] Создать каркас Flutter Web в `frontend/` (`flutter create --platforms web`) и добавить в `frontend/pubspec.yaml`: `flutter_riverpod`, `dio`, `web_socket_channel`, `flutter_map`, `latlong2`; настроить `frontend/analysis_options.yaml`
- [ ] T004 [P] Создать `docker-compose.yml`: сервисы `postgres` (PostgreSQL 16 + PostGIS + pgvector), `redis`, `langfuse`, `api`, `worker`; образы зафиксированы тегами/дайджестами (Принцип II); тома для БД, артефактов и кэша тайлов
- [ ] T005 [P] Создать `.env.example` со всеми переменными окружения без значений секретов: `DATABASE_URL`, `REDIS_URL`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LLM_PROVIDER` (`openai`|`ollama`), `LLM_MODEL`, `EMBEDDING_MODEL`, `OLLAMA_BASE_URL`, `SEED`, `OSM_USER_AGENT`, `ARTIFACTS_DIR`
- [ ] T006 [P] Создать `.github/workflows/ci.yml` с блокирующими гейтами: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest --cov` (порог 80%), `alembic check`, secret scanning, `dart format --set-exit-if-changed`, `flutter analyze`
- [ ] T007 [P] Инициализировать Alembic: `backend/alembic.ini` и `backend/alembic/env.py` (async engine, `target_metadata` из `glist.db.models`)
- [ ] T008 [P] Создать `backend/tests/conftest.py`: фикстура PostGIS-контейнера (testcontainers), async-сессия БД с откатом, тестовый HTTP-клиент, подключение мок-провайдера LLM
- [ ] T009 [P] Создать smoke-тест запуска клиента `frontend/test/app_smoke_test.dart`
- [ ] T010 Создать `README.md` с командами запуска стенда (ссылка на [quickstart.md](./quickstart.md)) и описанием структуры репозитория

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: общая инфраструктура, без которой не может начаться ни одна пользовательская история

**⚠️ CRITICAL**: ни одна задача Phase 3+ не начинается до завершения этой фазы

- [ ] T011 Настроить конфигурацию приложения `backend/src/glist/config.py`: модель `pydantic-settings` со всеми переменными из `.env.example`; секреты только из окружения (Принцип IX)
- [ ] T012 Создать доступ к БД `backend/src/glist/db/session.py`: async engine, `async_sessionmaker`, зависимость сессии для FastAPI и для ARQ-задач
- [ ] T013 Создать SQLModel-модели `user`, `query`, `clarification_request`, `analysis_run` в `backend/src/glist/db/models.py` с ограничениями из [data-model.md](./data-model.md): `status` ∈ `ожидает|в обработке|завершён|не прошёл проверку|данные недоступны|ошибка`, `attempt_count` CHECK `between 0 and 1`, `parent_run_id`, `reused_result_id`, `trace_id NOT NULL`, `settings_hash`, `params_hash`, `seed`, `rag_index_version`, `is_reference`, `is_pinned`, `retention_until`, `purge_notice_at`; индексы `(status, queue_position)`, `(query_id, created_at)`, `(params_hash)`
- [ ] T014 Создать SQLModel-модели `run_stage`, `validation_rejection`, `settings_profile` в `backend/src/glist/db/models.py`: `run_stage.description NOT NULL` (Принцип III), UNIQUE(`run_id`,`seq`); `validation_rejection.attempt_no` ∈ 1…2, `category` ∈ `analysis_error|data_quality`, `reasons` jsonb NOT NULL, `fixes_applied` jsonb, UNIQUE(`run_id`,`attempt_no`); `settings_profile` с PK `settings_hash` и неизменяемым `values` (Принцип II)
- [ ] T015 Создать SQLModel-модели `geocode_cache`, `area_of_interest`, `observation_dataset`, `run_dataset` в `backend/src/glist/db/models.py`: геометрии `geometry(Geometry, 4326)` с CHECK `ST_SRID = 4326`, GiST-индексы, `validity_fixed`, `buffer_m` обязателен при `aoi_type='buffer'`, `area_km2` из PostGIS, `cloud_cover` CHECK 0…100, UNIQUE(`source`,`collection`,`scene_id`), PK(`run_id`,`dataset_id`,`role`), `role` ∈ `start_period|end_period`
- [ ] T016 Создать SQLModel-модели `analysis_result`, `result_territory`, `change_zone` в `backend/src/glist/db/models.py`: UNIQUE(`params_hash`), `territories_count` CHECK `between 1 and 5`, `has_overlap`, `mode` ∈ `single|combined`, `citations`/`sources_summary` jsonb NOT NULL, `crs` default `EPSG:4326`, `validation_outcome` ∈ `passed|failed`; `change_zone.geom` — `geometry(MultiPolygon, 4326)` с CHECK SRID и `ST_IsValid`, `area_m2`/`area_km2` из PostGIS, `change_class` ∈ `retained|lost|new`, `cleaned`
- [ ] T017 Создать SQLModel-модели `artifact`, `artifact_deletion_log`, `knowledge_document`, `knowledge_chunk` в `backend/src/glist/db/models.py`: `is_heavy`, CHECK «`retention_until` заполнен только для `raw_scene`/`intermediate_ndvi`/`intermediate_classified`», `embedding vector(1536)`, UNIQUE(`document_id`,`ordinal`), UNIQUE(`checksum`)
- [ ] T018 Создать миграцию `backend/alembic/versions/0001_initial_schema.py`: `CREATE EXTENSION postgis`, `CREATE EXTENSION vector`, все таблицы, CHECK-ограничения SRID/валидности, GiST-индексы и частичные индексы (`is_reference`, `retention_until`)
- [ ] T019 Реализовать слой пространственных запросов `backend/src/glist/db/spatial_queries.py`: площадь через `ST_Area` в метрической проекции, `ST_Intersects`, `ST_Buffer`, `ST_IsValid`/`ST_MakeValid`, проверка SRID — **только** функции PostGIS, параметризованный SQL без конкатенации строк, docstring на каждую функцию (Принципы IV, IX, X)
- [ ] T020 Реализовать абстракцию `LLMProvider` в `backend/src/glist/llm/provider.py`: async `generate`, `stream`, `embeddings`; единые структуры запроса/ответа с моделью, temperature, seed, `max_tokens`, токенами и длительностью (Принцип VI)
- [ ] T021 [P] Реализовать `OpenAIProvider` в `backend/src/glist/llm/openai_provider.py` (ключ только из переменной окружения; таймауты и ошибки лимитов обрабатываются без проброса необработанных исключений в граф)
- [ ] T022 [P] Реализовать `OllamaProvider` в `backend/src/glist/llm/ollama_provider.py` (локальный режим без внешних ключей, та же структура ответа)

- [ ] T023 [P] Реализовать `MockLLMProvider` в `backend/src/glist/llm/mock_provider.py`: детерминированные ответы по seed и фиксированные сценарии валидации (успех, `analysis_error`, `data_quality`) — только для тестов и приёмочных прогонов
- [ ] T024 Создать фабрику провайдера и реестр промптов: `backend/src/glist/llm/__init__.py` (выбор по `LLM_PROVIDER`) и `backend/src/glist/llm/prompts/README.md` (правило версионирования `<agent>/<purpose>@vN.md`, хэш версии в трассе — Принципы VI, II)
- [ ] T025 Реализовать трассировку в `backend/src/glist/trace/langfuse.py`: трасса на запуск (`trace_id`), spans на узел графа, вызов инструмента, вызов LLM и публикацию события; при недоступности LangFuse запуск переводится в статус `ошибка`, а не продолжается молча (Принцип III)
- [ ] T026 Реализовать публикацию событий прогресса `backend/src/glist/trace/events.py` строго по [contracts/websocket-events.md](./contracts/websocket-events.md): Redis pub/sub, поле `seq`, обязательные `event`/`runId`/`occurredAt`, без секретов и внутренних деталей в payload
- [ ] T027 Реализовать `ArtifactStore` в `backend/src/glist/services/artifact_store.py`: запись в `ARTIFACTS_DIR/{run_id}/…`, расчёт `size_bytes` и `sha256`, установка `is_heavy = size_bytes > 100 МБ`, регистрация записи в таблице `artifact`
- [ ] T028 Реализовать индексатор RAG `backend/src/glist/rag/indexer.py`: чанки 800 символов с перекрытием 100, эмбеддинги через `LLMProvider.embeddings`, запись `knowledge_document`/`knowledge_chunk`, версия индекса и контрольная сумма корпуса
- [ ] T029 Реализовать поиск RAG `backend/src/glist/rag/retriever.py`: top-k = 5, порог косинусной близости 0,72, возврат ссылок (документ, версия, номер чанка) для `citations`; параметры извлечения фиксируются в метаданных запуска (Принцип VII)
- [ ] T030 Реализовать контракт состояния агентов `backend/src/glist/agents/state.py` строго по [contracts/agent-state.md](./contracts/agent-state.md): типизированные ключи, reducer `operator.add` для `stages`/`errors`/`artifacts`/`rag_context`, отсутствие бинарных данных и объектов Rasterio/GeoDataFrame в состоянии
- [ ] T031 Собрать каркас графа LangGraph в `backend/src/glist/agents/graph.py`: узлы `researcher` → `analyst` → `visualizer` → `validator` → `finalize`, условное ребро по [contracts/agent-state.md](./contracts/agent-state.md) (лимит переработки ≤1), чекпойнтер в PostgreSQL
- [ ] T032 Реализовать запись этапов каждым узлом графа (общая функция логирования этапа) в `backend/src/glist/agents/stages.py`: `seq`, `agent`, `stage_code`, `title`, непустой `description`, статусы `running|completed|failed|skipped` (Принцип III)
- [ ] T033 Настроить ARQ-worker `backend/src/glist/jobs/worker.py` (`max_jobs = 1`) и задачу выполнения анализа `backend/src/glist/jobs/analyze_run.py` (запуск графа, публикация событий, запись результата)
- [ ] T034 Реализовать очередь `backend/src/glist/services/queue_service.py`: не более одного активного запуска, постановка в очередь со `queue_position`, автостарт следующего после завершения (FR-021, SC-012)
- [ ] T035 Реализовать жизненный цикл запуска `backend/src/glist/services/run_service.py`: переходы статусов из [data-model.md](./data-model.md), терминальность состояний, сохранение `status_reason` и групп причин отклонения, `reused_result_id` при совпадении `params_hash` (FR-019, FR-020)
- [ ] T036 Создать приложение FastAPI `backend/src/glist/main.py` и зависимости `backend/src/glist/api/deps.py`: сессия БД, `get_current_user` (заглушка, принимающая `Authorization: Bearer …` — план снятия отклонения №1 из [plan.md](./plan.md)), реестр роутеров, обработчик ошибок без раскрытия внутренних деталей (Принцип IX)
- [ ] T037 Реализовать `GET /api/v1/health` в `backend/src/glist/api/health.py` со статусами `database`, `redis`, `langfuse` по схеме `Health` из [contracts/api.yaml](./contracts/api.yaml)
- [ ] T038 Реализовать WebSocket `backend/src/glist/api/ws.py`: подписка на Redis pub/sub по `runId`, трансляция событий по контракту, штатное закрытие после терминального события
- [ ] T039 [P] Создать contract-тесты API-каркаса `backend/tests/contract/test_api_contract.py`: соответствие реализованных эндпоинтов `contracts/api.yaml`, схема `Health`, форма `Error` для 404/422
- [ ] T040 [P] Создать unit-тесты пространственного слоя `backend/tests/unit/test_spatial_queries.py`: SRID-проверка, валидность и `ST_MakeValid`, площадь, пересечение, буфер — на реальном PostGIS-контейнере
- [ ] T041 Создать smoke-тест графа `backend/tests/integration/test_graph_smoke.py`: полный маршрут с мок-провайдером, проверка типизированного состояния и наличия непустых `description` у всех этапов

**Checkpoint**: фундамент готов — истории можно реализовывать параллельно

---

## Phase 3: User Story 1 - Типовой запрос: обоснованный ответ с картой (Priority: P1) 🎯 MVP

**Goal**: аналитик задаёт вопрос на естественном языке и получает интерактивную карту зон изменений, объяснение с источниками и датами съёмки, а также экспорт в PDF и GeoJSON.

**Independent Test**: отправить типовой запрос на контрольной территории с пригодными данными (`POST /api/v1/queries`, сценарий V1 из [quickstart.md](./quickstart.md)) и проверить: результат содержит три класса зон с площадями, объяснение содержит даты съёмки и источники, GeoJSON проходит проверку по схеме, PDF доступен для скачивания.

### Tests for User Story 1

- [ ] T042 [P] [US1] Contract-тест GeoJSON-экспорта `backend/tests/contract/test_geojson_export.py`: валидация по [contracts/geojson-export.schema.json](./contracts/geojson-export.schema.json), атрибуты `territory_name`/`change_class`/`area_km2`, наличие `crs` CRS84, диапазоны координат EPSG:4326
- [ ] T043 [P] [US1] Unit-тесты подбора данных `backend/tests/unit/test_tools_stac_search.py`: фильтр облачности ≤20 %, отбор сцен Sentinel-2 L2A и Landsat C2 L2, фиксация провенанса (источник, дата съёмки, разрешение, охват)
- [ ] T044 [P] [US1] Unit-тесты растровой обработки `backend/tests/unit/test_tools_raster_ops.py`: маски облачности SCL/QA_PIXEL, медианный композит в окне ±30 дней (+60 при нехватке), расчёт NDVI, маска воды NDWI — на синтетических растрах
- [ ] T045 [P] [US1] Unit-тесты классификации и зон `backend/tests/unit/test_tools_landcover.py`: порог NDVI из `settings_profile`, использование ESA WorldCover как базового слоя, формирование классов `retained`/`lost`/`new`, фильтр минимальной площади 1000 м², площади только из PostGIS
- [ ] T046 [P] [US1] Unit-тесты экспорта `backend/tests/unit/test_tools_export_geojson.py`: один `FeatureCollection`, отсутствие лишних атрибутов (`additionalProperties: false`), уникальность `territory_position`
- [ ] T047 [US1] Интеграционный тест US1 `backend/tests/integration/test_us1_typical_query.py`: полный маршрут графа с мок-провайдером на PostGIS-контейнере, проверка SC-001 (результат ≤60 с), SC-004 (источники и даты съёмки в ответе), SC-011-совместимость статусов
- [ ] T048 [P] [US1] Widget-тест клиента `frontend/test/query_flow_test.dart`: ввод запроса → отображение карты, объяснения и списка источников

### Implementation for User Story 1

- [ ] T049 [P] [US1] Реализовать инструмент подбора сцен `backend/src/glist/tools/stac_search.py`: STAC-каталоги Earth Search (Sentinel-2 L2A) и Planetary Computer (Landsat C2 L2), анонимный доступ, ссылки на COG, запись найденных сцен в `observation_dataset` с провенансом (R1, R2)
- [ ] T050 [P] [US1] Реализовать геокодирование `backend/src/glist/tools/geocode.py`: Nominatim/OSM с обязательным `User-Agent` и троттлингом ≤1 запрос/с, кэш в `geocode_cache`, построение буфера 1000 м средствами PostGIS (R5)
- [ ] T051 [P] [US1] Реализовать растровые операции `backend/src/glist/tools/raster_ops.py`: оконное чтение COG через Rasterio, маски облачности SCL/QA_PIXEL, медианный композит (окно ±30 дней с расширением до ±60), NDVI (B04/B08 и B4/B5), NDWI (R2, R3)
- [ ] T052 [P] [US1] Реализовать классификацию земного покрова `backend/src/glist/tools/landcover.py`: бинарная классификация растительности по порогу NDVI из `settings_profile`, сопоставление с эталоном ESA WorldCover (R3)
- [ ] T053 [P] [US1] Реализовать формирование зон изменений `backend/src/glist/tools/change_zones.py`: полигонализация `rasterio.features.shapes`, морфологическая очистка и фильтр 1000 м², запись геометрий в `change_zone` через `spatial_queries` (площади — только PostGIS), перепроецирование в EPSG:4326 (R4)
- [ ] T054 [P] [US1] Реализовать экспорт GeoJSON `backend/src/glist/tools/export_geojson.py`: один `FeatureCollection` с атрибутами `territory_name`, `change_class`, `area_km2`, `period_start`, `period_end` и блоком `crs` по [contracts/geojson-export.schema.json](./contracts/geojson-export.schema.json)
- [ ] T055 [P] [US1] Реализовать рендер превью-карты `backend/src/glist/tools/render_preview.py`: зоны изменений поверх базовых тайлов OSM (кэш тайлов на томе) в PNG, сохранение через `ArtifactStore` как `preview_png` (R13)
- [ ] T056 [US1] Реализовать сервис экспорта `backend/src/glist/services/export_service.py`: PDF (ReportLab) — карта со всеми территориями, раздельные таблицы статистики по каждой территории, суммарная таблица с пометкой «сумма по территориям (с возможным пересечением)», объяснение с привязкой выводов к территориям, список источников (FR-014, SC-006)
- [ ] T057 [US1] Реализовать формирование объяснения `backend/src/glist/services/explanation_service.py`: текст для неспециалиста, обязательные `citations` из RAG, явный отказ с сообщением при отсутствии обоснования — без выдумывания нормативных значений (FR-009, Принцип VII)

- [ ] T058 [US1] Реализовать агента Researcher `backend/src/glist/agents/researcher.py`: извлечение территории, периода, типа анализа; запрос уточнения при неоднозначности (FR-002); геокодирование; подбор пригодных сцен; запись этапов с описанием работы
- [ ] T059 [US1] Реализовать агента Analyst `backend/src/glist/agents/analyst.py`: композит → NDVI → бинарная классификация → зоны изменений → запись геометрий и расчёт площадей в PostGIS; заполнение `territory_stats` и `aggregate` с пометкой о возможном пересечении; описание каждого этапа
- [ ] T060 [US1] Реализовать агента Visualizer `backend/src/glist/agents/visualizer.py`: формирование `map_layers`, превью PNG, финального GeoTIFF (`final_geotiff`, бессрочное хранение), объяснения с `citations` и списком источников, привязка выводов к территориям (FR-008, FR-009, FR-014)
- [ ] T061 [US1] Реализовать агента Validator `backend/src/glist/agents/validator.py`: проверки валидности геометрий, баланса классов, минимальной площади, качества данных; заполнение `validation.outcome`, `category`, `reasons`; запись в `validation_rejection` (FR-007)
- [ ] T062 [US1] Собрать маршрут графа для US1 `backend/src/glist/agents/graph.py`: `researcher → analyst → visualizer → validator → finalize`, сохранение `analysis_result`/`result_territory`/`change_zone`, создание записи `settings_profile` по `settings_hash`, фиксация `seed`/`params_hash`/`trace_id`
- [ ] T063 [US1] Реализовать API запросов `backend/src/glist/api/queries.py`: `POST /api/v1/queries` (валидация `QueryCreate` из контракта, постановка запуска в очередь), `GET /api/v1/queries` (история с курсором), `GET /api/v1/queries/{queryId}`
- [ ] T064 [US1] Реализовать API запусков `backend/src/glist/api/runs.py`: `GET /api/v1/runs/{runId}`, `GET /api/v1/runs/{runId}/stages`, `GET /api/v1/runs/{runId}/result` (409, пока результат не готов; 200 для статусов `завершён` и `не прошёл проверку`)
- [ ] T065 [US1] Реализовать API экспорта `backend/src/glist/api/exports.py`: `POST /api/v1/runs/{runId}/exports`, `GET /api/v1/runs/{runId}/exports`, `GET /api/v1/artifacts/{artifactId}/content` (410 при удалённом тяжёлом артефакте — FR-022)
- [ ] T066 [P] [US1] Реализовать API-клиент и DTO клиента `frontend/lib/core/api_client.dart` и `frontend/lib/shared/models/` (`territoryName`, `changeClass`, `areaKm2`, статусы запуска) на dio, с обработкой кодов 409/410
- [ ] T067 [P] [US1] Реализовать экран ввода запроса `frontend/lib/features/query/` (Riverpod-провайдеры, отправка запроса, отображение ошибок валидации)
- [ ] T068 [P] [US1] Реализовать карту `frontend/lib/features/map/`: `flutter_map` с базовым слоем OSM, тремя слоями зон изменений (включение/отключение), легендой и площадями зон, масштабирование и панорамирование (FR-008)
- [ ] T069 [US1] Реализовать экран результата `frontend/lib/features/result/`: объяснение, список источников с датами съёмки, таблица площадей, причины отклонений валидатора (SC-004, SC-011)
- [ ] T070 [US1] Реализовать экспорт на клиенте `frontend/lib/features/export/`: запрос формирования и скачивание PDF/GeoJSON (FR-014)

**Checkpoint**: US1 работает независимо — это MVP; проверяется сценариями V1 и V8 из [quickstart.md](./quickstart.md)

---

## Phase 4: User Story 2 - История запросов и открытие прошлых результатов (Priority: P2)

**Goal**: аналитик видит прошлые запросы со статусами и открывает любой прежний результат без повторного анализа.

**Independent Test**: выполнить два запроса, перезапустить клиент, убедиться, что оба запроса есть в истории, а открытие записи восстанавливает карту, объяснение и источники за ≤5 с без создания нового запуска (сценарий V2 из [quickstart.md](./quickstart.md)).

### Tests for User Story 2

- [ ] T071 [P] [US2] Unit-тесты истории `backend/tests/unit/test_history_service.py`: курсорная пагинация, сортировка по дате, отображение статусов, пометка `purge_notice_at` и признак `is_pinned`
- [ ] T072 [US2] Интеграционный тест US2 `backend/tests/integration/test_us2_history.py`: открытие прошлого результата не создаёт нового запуска и укладывается в 5 с (SC-010); ошибка прошлого запуска отображается с причиной и вариантами продолжения

### Implementation for User Story 2

- [ ] T073 [US2] Реализовать сервис истории `backend/src/glist/services/history_service.py`: список запросов со статусами, курсорная пагинация, агрегирование статуса последнего запуска, доступ к сохранённому результату без пересчёта (FR-013)
- [ ] T074 [US2] Расширить `GET /api/v1/queries/{queryId}` в `backend/src/glist/api/queries.py`: возврат цепочки запусков по `parent_run_id` (схема `QueryDetail` из [contracts/api.yaml](./contracts/api.yaml))
- [ ] T075 [P] [US2] Реализовать экран истории `frontend/lib/features/history/`: список запросов с датами и статусами, переход к результату, отображение пометки о предстоящей очистке и признака «сохранить навсегда»
- [ ] T076 [US2] Реализовать открытие результата без анализа `backend/src/glist/services/run_service.py`: чтение `analysis_result` по существующему запуску, без постановки задачи в очередь (SC-010)

**Checkpoint**: история работает независимо от US1 (использует те же сохранённые результаты)

---

## Phase 5: User Story 3 - Уточняющий вопрос в контексте предыдущего запроса (Priority: P3)

**Goal**: аналитик уточняет территорию, а система берёт период и тип анализа из предыдущего запроса и добавляет территорию к комбинированному результату (или заменяет его).

**Independent Test**: после завершённого запроса отправить уточняющий вопрос с одной новой территорией и проверить, что период и тип анализа унаследованы, результат содержит **все** накопленные территории, а шестая территория отклоняется (сценарий V3 из [quickstart.md](./quickstart.md)).

### Tests for User Story 3

- [ ] T077 [P] [US3] Unit-тесты композиции результата `backend/tests/unit/test_result_composer.py`: режимы `add`/`replace`, раздельное хранение слоёв, флаг `has_overlap` через `ST_Intersects`, лимит 5 территорий с отказом, обязательная пометка суммарной статистики
- [ ] T078 [P] [US3] Unit-тесты именования территорий `backend/tests/unit/test_territory_naming.py`: имя из текста запроса, иначе «Область N» по порядку добавления, `name_source` ∈ `query_text|generated`
- [ ] T079 [US3] Интеграционный тест US3 `backend/tests/integration/test_us3_follow_up.py`: контекст включает все ранее накопленные территории (SC-009), третье уточнение добавляется ко всем предыдущим, шестая территория отклоняется, создаётся новый запуск с `parent_run_id` (FR-019 соблюдён)

### Implementation for User Story 3

- [ ] T080 [US3] Реализовать наследование контекста в агенте Researcher `backend/src/glist/agents/researcher.py`: перенос в `context` всех территорий, периода и типа анализа предыдущего результата; запрос уточнения при отсутствии контекста (FR-011, SC-009)
- [ ] T081 [US3] Реализовать сервис композиции результата `backend/src/glist/services/result_composer.py`: режим `add` (накопление всех территорий, слои раздельно, без геометрического объединения), режим `replace`, расчёт `has_overlap` через `ST_Intersects`, отказ при 6-й территории с предложением создать новый запрос, новый `analysis_run` со `parent_run_id` (FR-012, FR-013)
- [ ] T082 [US3] Реализовать именование территорий `backend/src/glist/services/territory_naming.py`: извлечение имени из текста запроса, при неудаче «Область N», сохранение в `area_of_interest.name` и `result_territory.territory_name` (FR-002)
- [ ] T083 [US3] Поддержать режимы уточнения в API `backend/src/glist/api/queries.py`: приём `parentQueryId` и `mode` (`add`/`replace`) по схеме `QueryCreate`, возврат `clarification` при нехватке контекста
- [ ] T084 [US3] Обновить экспорт для комбинированных результатов `backend/src/glist/services/export_service.py` и `backend/src/glist/tools/export_geojson.py`: раздельные таблицы по территориям, суммарная таблица с пометкой о пересечении, единый FeatureCollection со всеми территориями (FR-014)
- [ ] T085 [P] [US3] Реализовать выбор режима на клиенте `frontend/lib/features/result/`: переключатель «дополнить предыдущий» / «заменить предыдущий», отображение различимого оформления слоёв и пометки о пересечении
- [ ] T086 [US3] Обновить экран истории для цепочки уточнений `frontend/lib/features/history/`: отображение связи `parent_run_id` и переходов по цепочке

**Checkpoint**: уточняющие запросы работают независимо; проверяются сценарием V3

---

## Phase 6: User Story 4 - Наблюдение за работой агентов в реальном времени (Priority: P4)

**Goal**: аналитик видит этапы работы агентов с описаниями, очередь запусков и причины отклонения результата валидатором.

**Independent Test**: отправить запрос и зафиксировать поток событий; отдельно воспроизвести отклонение валидатором и повторное отклонение; проверить, что переработка ровно одна, а статус «не прошёл проверку» отличим от «завершён» и «ошибка» (сценарии V4 и V6 из [quickstart.md](./quickstart.md)).

### Tests for User Story 4

- [ ] T087 [P] [US4] Contract-тест WebSocket-событий `backend/tests/contract/test_ws_events.py`: обязательные поля `event`/`runId`/`occurredAt`, состав типов событий и payload строго по [contracts/websocket-events.md](./contracts/websocket-events.md)
- [ ] T088 [P] [US4] Unit-тесты планировщика переработки `backend/tests/unit/test_rework_policy.py`: переработка только при `analysis_error` и `attempt_no == 0`; при `data_quality` переработки нет; повторная переработка запрещена
- [ ] T089 [US4] Интеграционные тесты US4 `backend/tests/integration/test_us4_validation_queue.py`: одна переработка при `analysis_error`; мгновенный статус «не прошёл проверку» при `data_quality`; обе группы причин при повторном отклонении; ровно один активный запуск и автостарт следующего (SC-012); сохранение обеих групп причин в `validation_rejection`
- [ ] T090 [P] [US4] Widget-тест прогресса `frontend/test/progress_test.dart`: отображение этапов с описаниями, индикация отклонения и переработки, отображение очереди

### Implementation for User Story 4

- [ ] T091 [US4] Эмитить события этапов из узлов графа `backend/src/glist/agents/*.py` и `backend/src/glist/trace/events.py`: `run.stage.started`/`completed`/`failed`, не чаще одного события на этап, поле `seq` (FR-010, SC-007)
- [ ] T092 [US4] Реализовать API очереди `backend/src/glist/api/queue.py`: `GET /api/v1/queue` со схемой `QueueState` (активный запуск и ожидающие в порядке старта)
- [ ] T093 [US4] Реализовать правило переработки и сохранение причин `backend/src/glist/agents/graph.py`, `backend/src/glist/agents/validator.py`, `backend/src/glist/services/run_service.py`: условное ребро по категории причины, лимит `attempt_count ≤ 1`, запись `validation_rejection` для обеих попыток, заполнение `fixes_applied`, сохранение `rejection_category` (FR-007, FR-020, SC-011)
- [ ] T094 [US4] Реализовать эндпоинт трассировки `backend/src/glist/api/runs.py`: `GET /api/v1/runs/{runId}/trace` со схемой `TraceRef` (`traceId`, ссылка на LangFuse) (FR-016, SC-005)
- [ ] T095 [P] [US4] Реализовать клиент прогресса `frontend/lib/core/ws_client.dart` и `frontend/lib/features/progress/`: подписка на `/ws/runs/{id}`, список этапов с описаниями, индикация отклонения и переработки, дозапрос `GET /runs/{id}/stages` при обрыве соединения
- [ ] T096 [P] [US4] Реализовать отображение очереди на клиенте `frontend/lib/features/progress/`: активный запуск и список ожидающих, автоматическая замена активного трека (FR-021, SC-012)

**Checkpoint**: live-прогресс, очередь и правило переработки работают; проверяются сценариями V4, V6, V7

---

## Phase 7: User Story 5 - Понятный выход из ситуации «данных нет» (Priority: P5)

**Goal**: при отсутствии пригодных снимков система сообщает причину и предлагает альтернативы, позволяя продолжить работу без повторного ввода запроса.

**Independent Test**: воспроизвести запрос по территории и периоду без пригодных данных; убедиться, что названа причина, предложены не менее трёх альтернатив, а выбор альтернативы продолжает обработку (сценарий V5 из [quickstart.md](./quickstart.md)).

### Tests for User Story 5

- [ ] T097 [P] [US5] Unit-тесты альтернатив `backend/tests/unit/test_alternatives_service.py`: не менее трёх вариантов (другой период, другой источник, ближайшая дата), исключение вариантов, не содержащих пригодных сцен, сохранение параметров выбора
- [ ] T098 [US5] Интеграционный тест US5 `backend/tests/integration/test_us5_no_data.py`: статус `данные недоступны` с причиной; обработка продолжается после ответа без повторного ввода исходного запроса (FR-015, SC-008); при отказе от альтернатив запрос сохраняется в истории со статусом и причиной

### Implementation for User Story 5

- [ ] T099 [US5] Реализовать сервис альтернатив `backend/src/glist/services/alternatives_service.py`: поиск альтернатив (другой период ±60 дней, другой источник Sentinel-2 ↔ Landsat, ближайшая доступная дата), не менее трёх вариантов, фильтрация по облачности ≤20 % (FR-015)
- [ ] T100 [US5] Реализовать ответ на уточнение/альтернативу `backend/src/glist/api/clarifications.py`: `POST /api/v1/clarifications/{clarificationId}/answer` с валидацией `ClarificationAnswer`, обработка 409 для закрытых уточнений, продолжение обработки без повторного ввода (FR-015, SC-008)
- [ ] T101 [US5] Реализовать статус «данные недоступны» `backend/src/glist/services/run_service.py` и `backend/src/glist/agents/researcher.py`: заполнение `status_reason`, сохранение `clarification_request` с вариантами, запись в историю при отказе пользователя
- [ ] T102 [P] [US5] Реализовать интерфейс альтернатив `frontend/lib/features/query/`: отображение причины и списка альтернатив, выбор варианта, индикация продолжения обработки

**Checkpoint**: все пять историй функционально независимы

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: требования, не привязанные к одной истории: политика хранения, идемпотентность, приёмочный контур точности, гейты качества и документация

- [ ] T103 Реализовать сервис хранения `backend/src/glist/services/retention_service.py`: `is_heavy = size_bytes > 100 МБ`, `retention_until = завершение + 12 месяцев` только для `raw_scene`/`intermediate_ndvi`/`intermediate_classified`, исключение `is_reference` и `is_pinned`, `purge_notice_at` за 7 дней, бессрочное хранение остальных видов артефактов (FR-022, FR-023)
- [ ] T104 Реализовать ежедневную ARQ-задачу очистки `backend/src/glist/jobs/retention.py`: удаление только истёкших тяжёлых артефактов, запись каждого удаления в `artifact_deletion_log` (что, когда, причина, освобождённый объём) (FR-023, SC-013)
- [ ] T105 Реализовать управление признаком «сохранить навсегда» `backend/src/glist/api/runs.py` и `backend/src/glist/services/retention_service.py`: `PUT /api/v1/runs/{runId}/pin`, отмена ещё не выполненного планового удаления, исключение из очистки (FR-023)
- [ ] T106 [P] Написать тесты политики хранения и идемпотентности `backend/tests/unit/test_retention_service.py` и `backend/tests/integration/test_retention_idempotency.py`: сдвиг времени на 13 месяцев, частичная очистка по видам артефактов, сохранение `final_geotiff`/`preview_png`/GeoJSON/PDF, работа журнала, повторный запуск с тем же `params_hash` создаёт новый запуск со `reused_result_id` без дубликата результата (FR-019, SC-013)
- [ ] T107 Реализовать приёмочный контур SC-002 `backend/tests/acceptance/accuracy.py` и фикстуры `backend/tests/fixtures/`: 2 контрольные территории, не менее 50 проверочных участков на каждой, разметка вручную с опорой на ESA WorldCover; расчёт общей точности и macro-F1 по трём классам; отдельная CI-задача `acceptance` с отчётом и матрицей ошибок
- [ ] T108 Выполнить security hardening `backend/src/glist/api/`: `additionalProperties: false` во всех входных схемах, коды ошибок без внутренних деталей и SQL, отсутствие секретов в логах и трассировке, проверка secret scanning в `.github/workflows/ci.yml` (Принцип IX)
- [ ] T109 Настроить полный набор блокирующих гейтов качества в `backend/pyproject.toml` и `.github/workflows/ci.yml`: покрытие ≥80% по `tools`/`services` с `omit = ["*/agents/*"]`, `mypy --strict`, `ruff check`/`ruff format --check`, `alembic check`, `dart format --set-exit-if-changed`, `flutter analyze` (Принцип VIII)
- [ ] T110 Подготовить регламент проверки производительности `docs/perf-validation.md`: методика измерения SC-001 (≤60 с в 9 из 10 запусков) и SC-007 (первый этап ≤5 с, обновления ≤5 с), состав тестовых территорий, шаблон протокола измерений
- [ ] T111 Добавить структурированное логирование и уточнение причин статусов `backend/src/glist/trace/logging.py` и `backend/src/glist/services/run_service.py`: `run_id`, `trace_id`, этап, длительность; причины терминальных статусов без внутренних деталей в API-ответах (Принцип III)
- [ ] T112 [P] Обновить документацию модулей `backend/README.md`, `frontend/README.md` и `docs/glossary.md`: зона ответственности каждого агента и инструмента, канонические термины (статусы запуска, классы `retained|lost|new`, режимы `single|combined`, «сумма по территориям (с возможным пересечением)») — Принципы V, X
- [ ] T113 [P] Выполнить локализацию и UX-полировку `frontend/lib/shared/`: русские подписи статусов и уведомлений (включая предстоящую очистку), пустые и загрузочные состояния всех экранов
- [ ] T114 Провести финальную сквозную проверку по [quickstart.md](./quickstart.md) сценариями V1–V10 и зафиксировать протокол результатов в `docs/validation-report.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: без зависимостей — можно начинать немедленно; T002 и T004 блокируют работу стенда
- **Phase 2 (Foundational)**: зависит от Phase 1 и **блокирует все пользовательские истории**. Критический путь: T013→T018 (модели и миграция), T020→T024 (провайдер и промпты), T030→T031 (состояние и граф), T033→T035 (worker и жизненный цикл запуска)
- **Phase 3–7 (истории)**: все зависят только от Phase 2; далее параллельно или последовательно по приоритету P1→P5
- **Phase 8 (Polish)**: T107 (приёмочный контур SC-002) требует готовой US1; T106 требует T103–T105; T114 требует завершённых всех историй

### User Story Dependencies

- **US1 (P1)**: после Phase 2 — зависимостей от других историй нет (MVP)
- **US2 (P2)**: после Phase 2; использует сохранённые результаты, поэтому полноценно проверяется на данных US1, но реализуется независимо (сервис истории, API, экран)
- **US3 (P3)**: после Phase 2; логически расширяет US1 (наследование контекста, композиция результата), но проверяется отдельным сценарием
- **US4 (P4)**: после Phase 2; отображает события, которые производят узлы графа из US1, поэтому интерфейс прогресса демонстрируется после US1
- **US5 (P5)**: после Phase 2; использует подбор данных из US1 как источник информации о пригодности сцен

### Within Each User Story

- Тесты пишутся до реализации и должны падать до неё (обязательный гейт конституции)
- Модели → сервисы → эндпоинты → интеграция
- Backend готов раньше frontend в пределах одной истории
- Проверка истории выполняется до перехода к следующему приоритету

### Parallel Opportunities

- Все Setup-задачи с [P] (T003–T009) выполняются параллельно
- Все Foundational-задачи с [P] (T021–T023, T039–T040) выполняются параллельно
- После Phase 2 истории могут вестись параллельно разными исполнителями
- Внутри US1 параллельно: T042–T046 (тесты), T049–T055 (инструменты, разные файлы), T066–T068 (клиент)
- Внутри US4 параллельно: T087, T088, T090, T095, T096

---

## Parallel Example: User Story 1

```bash
# Инструменты US1 — разные файлы, можно запускать одновременно:
Task: "Инструмент подбора сцен backend/src/glist/tools/stac_search.py (T049)"
Task: "Геокодирование backend/src/glist/tools/geocode.py (T050)"
Task: "Растровые операции backend/src/glist/tools/raster_ops.py (T051)"
Task: "Классификация земного покрова backend/src/glist/tools/landcover.py (T052)"
Task: "Зоны изменений backend/src/glist/tools/change_zones.py (T053)"
Task: "Экспорт GeoJSON backend/src/glist/tools/export_geojson.py (T054)"

# Тесты US1 — разные файлы:
Task: "Contract-тест GeoJSON backend/tests/contract/test_geojson_export.py (T042)"
Task: "Unit-тесты подбора данных backend/tests/unit/test_tools_stac_search.py (T043)"
Task: "Unit-тесты растровой обработки backend/tests/unit/test_tools_raster_ops.py (T044)"
```

## Parallel Example: User Story 4

```bash
Task: "Contract-тест WebSocket-событий backend/tests/contract/test_ws_events.py (T087)"
Task: "Unit-тесты планировщика переработки backend/tests/unit/test_rework_policy.py (T088)"
Task: "Клиент прогресса frontend/lib/core/ws_client.dart (T095)"
Task: "Отображение очереди frontend/lib/features/progress/ (T096)"
```

---

## Implementation Strategy

### MVP First (только User Story 1)

1. Завершить Phase 1 (Setup) и Phase 2 (Foundational) — без них истории не начинаются
2. Завершить Phase 3 (US1): вопрос → карта + объяснение + источники + экспорт
3. **ОСТАНОВИТЬСЯ И ПРОВЕРИТЬ**: сценарии V1 и V8 из [quickstart.md](./quickstart.md)
4. Продемонстрировать: аналитик получает обоснованный ответ с картой, экспорт PDF и GeoJSON работает

### Incremental Delivery

1. Setup + Foundational → фундамент готов
2. US1 → проверка → демонстрация (**MVP**)
3. US2 → проверка (история без повторного анализа, SC-010) → демонстрация
4. US3 → проверка (лимит 5 территорий, все накопленные территории в контексте, SC-009) → демонстрация
5. US4 → проверка (одна переработка, очередь, SC-007, SC-012) → демонстрация
6. US5 → проверка (альтернативы без повторного ввода, SC-008) → демонстрация
7. Phase 8 → политика хранения (SC-013), приёмочный контур SC-002, гейты качества

### Parallel Team Strategy

With multiple developers:

1. Команда совместно проходит Setup и Foundational
2. После Foundational:
   - Разработчик A: US1 (MVP, критический путь)
   - Разработчик B: US2 и US5
   - Разработчик C: US4 (прогресс, очередь, переработка)
   - Разработчик D: US3 после готовности композиции результата
3. Frontend-задачи ведутся параллельно соответствующим backend-задачам: контракты уже зафиксированы в [contracts/](./contracts/), поэтому клиент можно разрабатывать против них

---

## Notes

- [P] = разные файлы, нет зависимостей от незавершённых задач
- Метки [US1]…[US5] обеспечивают трассируемость задач к историям из [spec.md](./spec.md)
- Тестовые задачи **обязательны** (Принцип VIII конституции), а не опциональны как в шаблоне по умолчанию
- Приёмочный контур SC-002 (T107) вынесен в отдельную задачу и не влияет на метрику покрытия ≥80%
- T108 фиксирует требования Принципа IX; JWT-аутентификация остаётся отклонением №1 из [plan.md](./plan.md) → Complexity Tracking и в объём этой фичи не входит
- Каждый запуск анализа обязан иметь `trace_id`, а каждый этап — непустое описание (Принципы III)
- Геометрии и площади — только через PostGIS; в состоянии графа никогда не хранятся растры (Принципы IV, V)
- Коммит после каждой задачи или логической группы; остановка на чекпоинтах для независимой проверки истории
- Избегать: задач без пути к файлу, конфликтов по одному файлу между [P]-задачами, скрытых зависимостей между историями