# Implementation Plan: Базовая платформа геопространственного анализа

**Branch**: `001-geo-analysis-platform` | **Date**: 2026-09-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-geo-analysis-platform/spec.md`

**Note**: План заполнен командой `/speckit-plan` (процедура — `.clinerules/workflows/speckit-plan.md`).

## Summary

Пользователь задаёт вопрос о геопространственных изменениях на естественном языке и получает
интерактивную карту зон изменений, текстовое объяснение с источниками и экспорт в PDF/GeoJSON.

**Технический подход**: Flutter Web (Riverpod + dio + web_socket_channel) поверх FastAPI; анализ
выполняется графом агентов на LangGraph (Researcher → Analyst → Visualizer → Validator; обмен только
через типизированное общее состояние; не более одной переработки). Данные наблюдений подбираются
автоматически по STAC-каталогам (Sentinel-2 L2A, Landsat C2 L2); чтение выполняется окнами по COG
через Rasterio, поэтому скачивание сцен не требуется. NDVI, маска воды и бинарная классификация
растительности считаются на растрах (Rasterio/GeoPandas), после чего полигоны зон изменений
(сохранившиеся / потерянные / новые) записываются в PostGIS в EPSG:4326, где выполняются **все**
геометрические операции (площади, пересечения территорий, буферы — только функциями PostGIS).
Текстовое объяснение формируется по результатам анализа с обязательной опорой на RAG-контекст
доменных документов (pgvector). Фоновая обработка и очередь «ровно один активный запуск» — Redis +
ARQ. Каждый шаг агента, вызов инструмента и вызов LLM трассируются в LangFuse; провенанс и параметры
воспроизводимости фиксируются в метаданных запуска и результата.

**Ключевые решения, перенесённые из спецификации**: лимит 5 территорий в комбинированном результате;
не более одной переработки валидатором (только при причине «ошибка анализа»); хранение артефактов
≤100 МБ, метаданных, объяснения, GeoJSON и превью — бессрочно, сырых снимков и промежуточных растров —
12 месяцев; признаки `is_reference` и `is_pinned` — бессрочное хранение.

## Technical Context

**Language/Version**: Python 3.11+ (backend; зависимости закреплены `uv.lock`), Dart 3.x / актуальный стабильный Flutter (frontend; только Flutter Web)

**Primary Dependencies**: FastAPI, SQLModel, Alembic, Pydantic v2 + pydantic-settings, LangGraph (+ персистентные чекпойнты в PostgreSQL), LangFuse SDK, Rasterio, GeoPandas, numpy, httpx, pystac-client, ARQ + redis-py, ReportLab, matplotlib, scikit-learn (только приёмочный контур SC-002, не в рантайме анализа); Flutter: Riverpod, dio, web_socket_channel, flutter_map; тестовые: pytest, pytest-asyncio, pytest-cov, testcontainers

**Storage**: PostgreSQL 16 + PostGIS (`geometry(...,4326)`, GiST-индексы) + pgvector (эмбеддинги RAG) в одном кластере; Redis (очередь ARQ, pub/sub для событий прогресса); артефакты — локальный том через абстракцию `ArtifactStore` (`/data/artifacts/{run_id}/…`, в перспективе S3-совместимый бэкенд)

**Testing**: pytest + pytest-asyncio + pytest-cov (порог ≥80% по инструментам и бизнес-логике; пакеты агентов исключены из метрики), интеграционные тесты графа LangGraph на реальном PostGIS-контейнере с мок-провайдером LLM, contract-тесты по `contracts/api.yaml`; Flutter: `flutter_test`, `flutter analyze`, `dart format --set-exit-if-changed`; отдельный приёмочный харнесс SC-002 (scikit-learn: accuracy и F1) на контрольной разметке

**Target Platform**: Linux-контейнеры (Docker Compose: `postgres+postgis+pgvector`, `redis`, `langfuse`, `api`, `worker`); клиент — браузеры Chrome/Edge/Firefox/Safari (Flutter Web)

**Project Type**: web application — Option 2 шаблона (backend + frontend)

**Performance Goals**: полный результат типового запроса ≤60 с в 9 из 10 запусков при пригодных данных (SC-001); первый этап прогресса ≤5 с после отправки (SC-007); обновление этапов не реже раза в 5 с; открытие результата из истории ≤5 с (SC-010); ровно один активный анализ, остальные — в очереди с автостартом (SC-012)

**Constraints**: все координаты EPSG:4326 (проверка SRID на уровне БД и валидации); геометрические операции над хранимыми данными — только функциями PostGIS; лимит 5 территорий в комбинированном результате; облачность пригодных сцен ≤20%; типовая территория ≤~300 км², период ≤10 лет; политика хранения (артефакты ≤100 МБ и метаданные/объяснение/GeoJSON/превью — бессрочно; сырые снимки и промежуточные растры — 12 месяцев); секреты только через переменные окружения; валидация всех входов Pydantic v2; трассировка каждого шага в LangFuse обязательна

**Scale/Scope**: один локальный пользователь (аутентификация вне объёма фичи); архив наблюдений до 10 лет; ≤5 территорий на результат; 5 пользовательских историй, 23 функциональных требования, 13 критериев успеха

**NEEDS CLARIFICATION**: нет — все неопределённости Технического контекста разрешены в `research.md` (Phase 0)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Источник гейтов — `.specify/memory/constitution.md` (v1.1.0). Предварительный итог: **PASS** по всем
принципам, кроме четырёх обоснованных отклонений, зафиксированных в разделе Complexity Tracking.
Неразрешённых `NEEDS CLARIFICATION` нет, поэтому гейт Phase 0 пройден.

| Гейт (принцип / раздел конституции) | Как обеспечивается планом | Статус |
|---|---|---|
| I. Научная обоснованность | Каждое решение Phase 0 оформлено как Decision / Rationale / Alternatives; NDVI-пороги и бинарная классификация растительности опираются на публикации и эталон ESA WorldCover; неопределённость и параметры фиксируются в метаданных результата | PASS |
| II. Воспроизводимость | Lock-файлы (`uv.lock`, `pubspec.lock`), образы Compose с digest, `seed` и `settings_hash` в `analysis_run`, `params_hash` для повторного запуска, полный провенанс наборов данных в `observation_dataset` | PASS |
| III. Прозрачность и трассируемость | LangFuse: трасса на запуск (`analysis_run.trace_id`), spans на каждый узел графа, инструмент и вызов LLM; `run_stage` с reasoning-описаниями; WebSocket-события прогресса; причины отклонений валидатора сохраняются по каждой попытке | PASS |
| IV. Геопространственная корректность (NON-NEGOTIABLE) | `geometry(...,4326)` + CHECK на SRID, `ST_IsValid`/`ST_MakeValid` при загрузке, **все** геометрические операции (площади, пересечения территорий, буферы) — только PostGIS; растровая обработка — Rasterio/GeoPandas; провенанс (источник, дата съёмки, разрешение, охват, облачность) в `observation_dataset` | PASS |
| V. Модульные агенты и общее состояние | Отдельные модули `agents/{researcher,analyst,visualizer,validator}.py`, типизированный контракт состояния `agents/state.py`, обмен только через State, оркестрация LangGraph (граф — единственный источник истины), тестируемость каждого узла фикстурой State, содержательный Validator с правилом ≤1 переработки | PASS |
| VI. Абстракция LLM-провайдера | Все вызовы LLM и эмбеддинги — только через `llm/provider.py`: реализации OpenAI и Ollama, выбор по переменной окружения, логирование модели/параметров/токенов в трассу, версионируемые промпты, мок-провайдер в тестах | PASS |
| VII. Обязательная опора на RAG | Векторный поиск по доменным документам (нормы, формулы, история) в pgvector, эмбеддинги через `LLMProvider`, обязательные ссылки на фрагменты в объяснении, явный отказ при отсутствии обоснования | PASS |
| VIII. Качество через тесты (NON-NEGOTIABLE) | pytest: unit для инструментов и бизнес-логики (покрытие ≥80%, пакеты агентов исключены), интеграционные тесты графа с мок-LLM на PostGIS-контейнере; CI-гейты: ruff check + ruff format, mypy --strict, pytest-cov, проверка миграций Alembic, secret scanning, `dart format` + `flutter analyze` | PASS |
| IX. Безопасность и валидация | Pydantic v2 на всех входах API и фоновых задач, параметризованные запросы SQLModel, валидация SRID/валидности геометрий, секреты только в переменных окружения + secret scanning в CI. JWT-аутентификация **отложена** вместе с фичей аутентификации (spec → Out of Scope) → Complexity Tracking, строка 1 | PASS с отклонением |
| X. Строгая типизация, стиль, документация | mypy --strict, docstring (стиль Google) на публичных API, ruff format, `dart format` + `flutter analyze` без предупреждений, snake_case в Python / camelCase в Dart | PASS |
| Технологический стек (раздел «Технологические стандарты…») | Python 3.11+/FastAPI/SQLModel/Alembic, LangGraph, LangFuse, PostgreSQL 16 + PostGIS + pgvector, Redis + ARQ, Rasterio/GeoPandas, Pydantic v2, Flutter + Riverpod/dio/web_socket_channel, EPSG:4326 — используются как обязательные. Сверх перечня добавляются `flutter_map`, ReportLab, matplotlib, scikit-learn (Complexity Tracking, строки 2–4) | PASS с отклонениями |
| Процесс (CI-гейты, Definition of Done) | План предусматривает полный набор блокирующих гейтов CI, ревизии Alembic, провенанс и трассировку как часть Definition of Done; любые отклонения допускаются только через Complexity Tracking | PASS |

### Post-Design Re-Check (после Phase 1)

Повторная оценка гейтов после генерации `research.md`, `data-model.md`, `contracts/*` и `quickstart.md`:

- **Принцип IV (NON-NEGOTIABLE)**: все геометрии в модели данных объявлены как `geometry(…,4326)` с CHECK на
  SRID и валидность; контракт состояния требует проверки геометрий средствами PostGIS; quickstart (V8)
  проверяет SRID и валидность SQL-запросами, площади сверяются со `ST_Area` → **PASS**.
- **Принцип V**: `contracts/agent-state.md` фиксирует закрытый набор типизированных ключей, правило
  «только через общее состояние» и отсутствие бинарных данных в состоянии → **PASS**.
- **Принцип III**: `trace_id` на запуск, `run_stage.description`, `contracts/websocket-events.md` и
  `validation_rejection` обеспечивают трассируемость каждого шага и обеих групп причин → **PASS**.
- **Принцип II**: неизменяемый `settings_profile` по хэшу, `params_hash`, `seed`, провенанс в
  `observation_dataset` / `run_dataset` → **PASS**.
- **Принцип VII**: `knowledge_document` / `knowledge_chunk` (pgvector) и обязательное поле `citations`
  в результате → **PASS**.
- **Принцип VIII**: контракты проверяемы (OpenAPI, JSON Schema, contract-тесты), приёмочный контур SC-002
  отделён от метрики покрытия → **PASS**.
- **Принцип IX**: `additionalProperties: false` в схемах запросов, коды ошибок без внутренних деталей,
  секреты только в переменных окружения → **PASS**; JWT остаётся отклонением №1 с планом снятия.
- **Отклонения 2–4** (`flutter_map`, ReportLab/matplotlib, scikit-learn) подтверждены структурой проекта
  и сценариями quickstart; **новых отклонений Phase 1 не добавила** — Complexity Tracking не расширяется.

**Итог**: гейт Constitution Check пройден до и после проектирования (`PASS` с четырьмя обоснованными
отклонениями, зафиксированными выше).

## Project Structure

### Documentation (this feature)

```text
specs/001-geo-analysis-platform/
├── plan.md              # Этот файл (/speckit-plan)
├── spec.md              # Спецификация фичи (/speckit-specify)
├── research.md          # Phase 0: решения и альтернативы (/speckit-plan)
├── data-model.md        # Phase 1: сущности, поля, переходы состояний (/speckit-plan)
├── quickstart.md        # Phase 1: проверочные сценарии запуска (/speckit-plan)
├── checklists/
│   └── requirements.md  # Ревью требований (/speckit-specify)
└── contracts/           # Phase 1: контракты интерфейсов (/speckit-plan)
    ├── api.yaml                     # OpenAPI 3.1: REST API платформы
    ├── websocket-events.md          # Контракт WebSocket-событий прогресса
    ├── geojson-export.schema.json   # JSON Schema экспортируемого FeatureCollection
    └── agent-state.md               # Контракт общего состояния графа агентов
```

### Source Code (repository root)

```text
backend/
├── pyproject.toml                 # зависимости, конфигурация ruff/mypy/pytest (lock: uv.lock)
├── alembic.ini
├── alembic/versions/              # ревизии схемы; расширения PostGIS/pgvector — первой ревизией
├── src/glist/
│   ├── main.py                    # сборка FastAPI-приложения
│   ├── config.py                  # pydantic-settings: все настройки только из переменных окружения
│   ├── api/
│   │   ├── deps.py                # сессия БД, get_current_user (заглушка, совместимая с JWT)
│   │   ├── queries.py             # запросы, уточнения, альтернативы при отсутствии данных
│   │   ├── runs.py                # статус, этапы, причины отклонений, очередь, «сохранить навсегда»
│   │   ├── exports.py             # генерация и выдача PDF/GeoJSON
│   │   ├── artifacts.py           # превью-карта, финальный GeoTIFF
│   │   └── ws.py                  # WebSocket /ws/runs/{id} — прогресс
│   ├── agents/
│   │   ├── state.py               # типизированное общее состояние (контракт: contracts/agent-state.md)
│   │   ├── graph.py               # LangGraph: узлы, условные рёбра, лимит переработки
│   │   ├── researcher.py          # подбор данных, альтернативы (период/источник/ближайшая дата)
│   │   ├── analyst.py             # NDVI, классификация, зоны изменений
│   │   ├── visualizer.py          # слои карты, превью, подготовка экспорта
│   │   └── validator.py           # проверки, категория причины, правило ≤1 переработки
│   ├── llm/
│   │   ├── provider.py            # абстракция LLMProvider (generate/embeddings)
│   │   ├── openai_provider.py
│   │   ├── ollama_provider.py
│   │   ├── mock_provider.py       # только тесты и приёмочные прогоны
│   │   └── prompts/               # версионируемые промпты
│   ├── tools/
│   │   ├── stac_search.py         # подбор сцен Sentinel-2 L2A / Landsat C2 L2 по STAC
│   │   ├── geocode.py             # территория → полигон (Nominatim/OSM + кэш в БД)
│   │   ├── raster_ops.py          # композиты, NDVI, маска воды (Rasterio)
│   │   ├── landcover.py           # бинарная классификация растительности, ESA WorldCover
│   │   ├── change_zones.py        # полигонализация → запись геометрий в PostGIS
│   │   ├── render_preview.py      # превью-карта (matplotlib)
│   │   └── export_geojson.py      # FeatureCollection по контракту
│   ├── rag/
│   │   ├── indexer.py             # индексация доменных документов (нормы, формулы, история)
│   │   ├── retriever.py           # поиск в pgvector с фиксацией параметров
│   │   └── embeddings.py          # эмбеддинги через LLMProvider
│   ├── db/
│   │   ├── models.py              # SQLModel-модели (см. data-model.md)
│   │   ├── session.py
│   │   └── spatial_queries.py     # площади, пересечения, буферы — только функции PostGIS
│   ├── services/
│   │   ├── run_service.py         # жизненный цикл запуска, статусы, группы причин
│   │   ├── queue_service.py       # ровно один активный запуск, автостарт (SC-012)
│   │   ├── export_service.py      # PDF (ReportLab) и GeoJSON
│   │   └── retention_service.py   # политика хранения, уведомление за 7 дней, журнал удалений
│   ├── jobs/
│   │   ├── worker.py              # настройка ARQ (concurrency=1)
│   │   ├── analyze_run.py         # задача выполнения анализа
│   │   └── retention.py           # ежедневная очистка хранилища
│   └── trace/
│       ├── langfuse.py            # трассировка узлов, инструментов, LLM-вызовов
│       └── events.py              # события прогресса: Redis pub/sub → WebSocket
└── tests/
    ├── unit/                      # инструменты и бизнес-логика (метрика покрытия ≥80%)
    ├── integration/               # граф LangGraph с мок-LLM на PostGIS-контейнере
    ├── contract/                  # соответствие contracts/api.yaml
    ├── acceptance/                # харнесс SC-002: accuracy и F1 на контрольной разметке
    └── fixtures/                  # эталонная разметка, тестовые полигоны, ответы провайдера

frontend/
├── pubspec.yaml
├── analysis_options.yaml
└── lib/
    ├── main.dart
    ├── core/
    │   ├── api_client.dart        # dio + модели из contracts/api.yaml
    │   ├── ws_client.dart         # web_socket_channel: события прогресса
    │   └── providers.dart         # Riverpod: состояние приложения
    ├── features/
    │   ├── query/                 # ввод запроса и уточняющих вопросов
    │   ├── progress/              # live-наблюдение за этапами агентов
    │   ├── map/                   # flutter_map: слои зон изменений, легенда, площади
    │   ├── result/                # объяснение, источники, причины отклонений
    │   ├── history/               # история, цепочка parent_run_id, «сохранить навсегда»
    │   └── export/                # скачивание PDF/GeoJSON
    └── shared/
        ├── models/                # DTO (territoryName, changeClass, areaKm2, …)
        └── widgets/               # таблицы статистики, статусы, уведомления об очистке

docker-compose.yml                 # postgres+postgis+pgvector, redis, langfuse, api, worker
```

**Structure Decision**: выбран вариант «web application»: `backend/` — Python-сервис (`src/glist/`) и
`frontend/` — единая кодовая база Flutter Web (соответствует конституции «Python-бэкенд + Flutter-клиент»
и уточнению спецификации «на первом этапе только Web»). Внутри бэкенда модули разделены по зонам
ответственности так, чтобы каждый агент был отдельным модулем, а инструменты — независимо тестируемыми
единицами (Принципы V и VIII): `agents/` содержит только узлы графа и контракт состояния, предметная
логика — в `tools/`, доступ к данным — в `db/` (все геометрические операции только через PostGIS),
трассировка и события — в `trace/`.

## Complexity Tracking

> Отклонения от конституции допустимы только с обоснованием (Governance → «Обеспечение соответствия»).
> Ниже — полный перечень отклонений плана; других исключений нет.

| Отклонение (нарушаемое требование) | Почему необходимо | Почему отвергнута более простая альтернатива |
|---|---|---|
| 1. **Отсутствие JWT-аутентификации** (Принцип IX конституции: «Аутентификация MUST использовать JWT») | Спецификация прямо относит аутентификацию и управление пользователями к отдельной фиче (Out of Scope), а в этой фиче предполагается один локальный пользователь: без этого объём MVP растёт и упирается в управление ключами и регистрацию | Реализовать JWT сейчас: не требуется приёмкой, добавляет таблицы пользователей, эндпоинты входа, ротацию ключей и защиту от CSRF/утечек — при этом контракты API уже проектируются с зависимостью `get_current_user` и заголовком `Authorization`. **План снятия исключения**: в отдельной фиче аутентификации заменить реализацию зависимости на проверку JWT, добавить таблицу пользователей и эндпоинты входа — изменение контрактов не потребуется |
| 2. **Библиотека карт `flutter_map`** (раздел «Технологические стандарты»: перечень Flutter-зависимостей ограничен Riverpod/dio/web_socket_channel) | FR-008 требует интерактивную карту с масштабированием, панорамированием и управлением слоями; ни один из перечисленных в конституции пакетов рендер карт не обеспечивает | WebView + Leaflet: смешивает HTML/JS с Flutter и нарушает принцип единой кодовой базы Flutter; серверный рендер статичных картинок: не даёт интерактивности и не выполняет FR-008. `flutter_map` не дублирует функциональность существующего стека (это новая возможность), поэтому запрет «MUST NOT добавлять дублирующие зависимости» не нарушается |
| 3. **ReportLab и matplotlib** (серверный рендер PDF и превью-карты) | FR-014 требует PDF с картой и таблицами статистики; FR-013/SC-010 требуют открывать прошлый результат без повторного анализа, а после очистки растров (FR-022) единственным носителем картинки остаётся сохранённое превью | WeasyPrint: тянет системные библиотеки (pango/cairo) в образ и усложняет воспроизводимую сборку (Принцип II); клиентский рендер превью в Flutter: рассинхронизация с серверными данными и невозможность сформировать превью после очистки растров |
| 4. **scikit-learn** (в приёмочном контуре SC-002) | SC-002 требует общей точности ≥85% и F1 ≥70% по каждому из трёх классов на контрольной разметке — метрики должны считаться по общепринятым определениям, иначе приёмка спорна | Собственная реализация accuracy/F1: риск методического расхождения и спорной приёмки; библиотека используется **только** в `tests/acceptance/` и не входит в рантайм анализа, поэтому 60-секундный бюджет и состав рантайм-зависимостей не меняются |

Дополнительно для прозрачности (не отклонения, а вспомогательные библиотеки, не дублирующие стек):
`numpy` (расчёты по растрам), `httpx` (HTTP-клиент для STAC/OSM), `pystac-client` (обнаружение данных
по STAC — новая функциональность), `pytest-asyncio`, `testcontainers` (тестовый контур).
