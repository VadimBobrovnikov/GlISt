# GlISt — мультиагентная платформа геопространственного анализа

Пользователь задаёт вопрос о геопространственных изменениях на естественном языке и получает
обоснованный ответ: интерактивная карта зон изменений, текстовое объяснение с источниками,
экспорт в PDF/GeoJSON. Анализ выполняется графом агентов LangGraph
(Researcher → Analyst → Visualizer → Validator) с полной трассировкой в LangFuse.

**Статус**: фича `001-geo-analysis-platform` в разработке. Спецификация — [specs/001-geo-analysis-platform/spec.md](specs/001-geo-analysis-platform/spec.md).

## Быстрый старт

Полный сценарий проверки end-to-end — **[specs/001-geo-analysis-platform/quickstart.md](specs/001-geo-analysis-platform/quickstart.md)**.
Кратко:

```bash
# 1. Переменные окружения (секреты только здесь; см. .env.example)
cp .env.example .env        # и заполните CHANGE_ME_* значениями: openssl rand -hex 32

# 2. Стенд: postgres+postgis+pgvector, redis, minio, langfuse, api, worker
docker compose up -d --build

# 3. Схема БД (расширения postgis и pgvector создаются первой ревизией)
cd backend && uv run alembic upgrade head

# 4. Веб-клиент (Flutter Web; для flutter-команд на Windows с кириллицей в пути — см. quickstart, §7)
cd frontend && flutter pub get && flutter run -d edge
```

## Структура репозитория

```
backend/
├── src/glist/
│   ├── api/        # маршруты FastAPI, схемы запросов/ответов
│   ├── agents/     # узлы графа LangGraph: researcher, analyst, visualizer, validator, state
│   ├── llm/        # абстракция LLMProvider: OpenAI, Ollama, мок (тесты)
│   ├── tools/      # инструменты: STAC-поиск, NDVI, landcover, зоны, рендер, экспорт
│   ├── rag/        # индексация и поиск доменных знаний (pgvector)
│   ├── db/         # SQLModel-модели, сессии, пространственные запросы (только PostGIS)
│   ├── services/   # жизненный цикл запусков, очередь, экспорт, политика хранения
│   ├── jobs/       # фоновые задачи ARQ
│   └── trace/      # трассировка LangFuse, события прогресса
├── tests/          # unit / integration / contract / acceptance / fixtures
├── deploy/         # образ БД стенда (PostGIS + pgvector)
└── Dockerfile      # образ api/worker
frontend/           # Flutter Web (Riverpod, dio, web_socket_channel, flutter_map)
docker-compose.yml  # локальный стенд (все образы пинятся по digest)
specs/              # артефакты Spec Kit: spec, plan, tasks, research, data-model, contracts, quickstart
.specify/           # конституция проекта (.specify/memory/constitution.md) и скрипты Spec Kit
```

## Команды разработки

```bash
# Backend (Python 3.11+, инструменты через uv)
cd backend
uv sync --frozen
uv run ruff check .            # линт
uv run ruff format --check .   # формат
uv run mypy src/glist          # строгая типизация
uv run pytest --cov            # тесты, порог покрытия 80% (пакеты agents/ исключены)

# Frontend (Dart/Flutter; flutter-команды — из каталога frontend; при кириллице в пути
# используйте junction D:\glist, см. quickstart.md → «Известные ограничения стенда»)
cd frontend
dart format --set-exit-if-changed .
flutter analyze
flutter test
```

## CI

Каждый PR должен пройти гейты в [.github/workflows/ci.yml](.github/workflows/ci.yml):
`ruff check`, `ruff format --check`, `mypy --strict`, `pytest --cov` (≥80%),
`alembic upgrade head && alembic check`, secret scanning (gitleaks),
`dart format --set-exit-if-changed`, `flutter analyze`, `flutter test`.

## Документы проекта

| Документ | Ссылка |
|---|---|
| Конституция (незыблемые принципы) | [.specify/memory/constitution.md](.specify/memory/constitution.md) |
| Спецификация фичи | [specs/001-geo-analysis-platform/spec.md](specs/001-geo-analysis-platform/spec.md) |
| План реализации + отклонения | [specs/001-geo-analysis-platform/plan.md](specs/001-geo-analysis-platform/plan.md) |
| Задачи | [specs/001-geo-analysis-platform/tasks.md](specs/001-geo-analysis-platform/tasks.md) |
| Контракты API / WebSocket / GeoJSON | [specs/001-geo-analysis-platform/contracts/](specs/001-geo-analysis-platform/contracts/) |
| Быстрая проверка (quickstart) | [specs/001-geo-analysis-platform/quickstart.md](specs/001-geo-analysis-platform/quickstart.md) |

## Лицензия и данные

Источники данных — открытые каталоги (Sentinel-2, Landsat, ESA WorldCover, OSM); провенанс
каждого набора наблюдений фиксируется в результатах анализа.
