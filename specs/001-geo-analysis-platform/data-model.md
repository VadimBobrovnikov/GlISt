# Phase 1 Data Model: Базовая платформа геопространственного анализа

**Feature**: `001-geo-analysis-platform` | **Date**: 2026-09-26 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

СУБД: PostgreSQL 16 + PostGIS + pgvector (единый кластер). Все геометрии — `geometry(Geometry, 4326)`
с CHECK `ST_SRID(geom) = 4326`; **все** пространственные операции (площади, пересечения, буферы,
валидность) выполняются функциями PostGIS (Принцип IV конституции). Миграции — Alembic; первая ревизия
создаёт расширения `postgis` и `pgvector`.

## Обзор связей

```text
user 1──* query 1──* analysis_run (parent_run_id — самоссылка: цепочка уточнений)
                     │  1──* clarification_request  (уточнения и альтернативы)
                     │  1──1 analysis_result 1──* result_territory *──1 area_of_interest
                     │                                      └── 1──* change_zone
                     │  1──* run_stage
                     │  1──* validation_rejection
                     │  *──* observation_dataset (через run_dataset)
                     │  1──* artifact 1──* artifact_deletion_log
                     └─ settings_profile (по settings_hash) ; knowledge_document 1──* knowledge_chunk (pgvector)
```

## 1. `user`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `display_name` | text | not null, значение по умолчанию для локального пользователя |
| `is_local` | bool | not null, default true — в этой фиче единственный локальный пользователь |
| `created_at` | timestamptz | not null |

Модель вводится уже сейчас, чтобы отдельная фича аутентификации не требовала перестройки связей
(план снятия исключения в плане → Complexity Tracking, строка 1).

## 2. `query`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `user_id` | uuid FK → `user.id` | not null, on delete cascade |
| `text` | text | not null, 3…1000 символов; язык ru или en (иначе сообщение о поддерживаемых языках) |
| `language` | text | not null, `ru` \| `en` |
| `query_kind` | text | not null, `primary` \| `follow_up` (`follow_up` — уточняющий вопрос, FR-011) |
| `parent_query_id` | uuid FK → `query.id` | null для первого запроса; источник контекста для follow-up |
| `extracted` | jsonb | извлечённые территория, период, тип анализа (FR-002) |
| `status` | text | not null, зеркало статуса последнего запуска: `ожидает` \| `в обработке` \| `завершён` \| `не прошёл проверку` \| `данные недоступны` \| `ошибка` |
| `created_at`, `updated_at` | timestamptz | not null |

Индексы: `(user_id, created_at desc)` — список истории (US2), `(parent_query_id)`.

## 3. `clarification_request`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `query_id` | uuid FK → `query.id` | not null |
| `kind` | text | not null, `ambiguity` (FR-002) \| `no_data_alternatives` (FR-015) |
| `question_text` | text | not null |
| `options` | jsonb | не менее 3 вариантов для `no_data_alternatives` (FR-015) |
| `selected_option` | jsonb | null, пока не отвечен |
| `status` | text | not null, `ожидает ответа` \| `отвечен` \| `истёк` |
| `created_at`, `answered_at` | timestamptz | `answered_at` null до ответа |

## 4. `analysis_run`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | идентификатор запуска; ключ трассировки (FR-016, SC-005) |
| `query_id` | uuid FK → `query.id` | not null |
| `parent_run_id` | uuid FK → `analysis_run.id` | null для первого запуска; цепочка уточнений (FR-013) |
| `reused_result_id` | uuid FK → `analysis_result.id` | not null при повторной отправке того же запроса (FR-019) |
| `status` | text | not null: `ожидает` \| `в обработке` \| `завершён` \| `не прошёл проверку` \| `данные недоступны` \| `ошибка` — взаимоисключающие состояния (FR-020) |
| `status_reason` | text | человекочитаемая причина для терминальных статусов |
| `queue_position` | int | null, если не в очереди; заполняется при `ожидает` (FR-021) |
| `queued_at`, `started_at`, `finished_at` | timestamptz | порядок постановки и завершения |
| `attempt_count` | smallint | not null, default 0; CHECK `attempt_count between 0 and 1` (не более одной переработки, FR-007) |
| `rejection_category` | text | null \| `analysis_error` \| `data_quality` — категория причины (FR-007) |
| `trace_id` | text | not null — идентификатор трассы LangFuse (Принцип III) |
| `settings_hash` | text | not null FK → `settings_profile.settings_hash` |
| `params_hash` | text | not null — sha256(AOI + период + настройки + источники), R16 |
| `seed` | bigint | not null — seed воспроизводимости (Принцип II) |
| `rag_index_version` | text | not null — версия индекса RAG (Принцип VII) |
| `model_versions` | jsonb | not null — модель LLM, модель эмбеддингов, версии промптов |
| `is_reference` | bool | not null default false — эталонный запуск контрольной территории (SC-002, SC-013) |
| `is_pinned` | bool | not null default false — «сохранить навсегда» (FR-023) |
| `retention_until` | timestamptz | null = бессрочно; заполняется только для тяжёлых артефактов (FR-022) |
| `purge_notice_at` | timestamptz | null; выставляется за 7 дней до планового удаления (FR-023) |
| `created_at` | timestamptz | not null |

Индексы: `(status, queue_position)`, `(query_id, created_at)`, `(params_hash)`, `(is_reference) where is_reference`, `(retention_until) where retention_until is not null`.

## 5. `run_stage`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `run_id` | uuid FK → `analysis_run.id` | not null, on delete cascade |
| `seq` | smallint | not null — порядок этапа в запуске |
| `attempt_no` | smallint | not null, default 0 — номер итерации (0 — первая, 1 — переработка) |
| `agent` | text | not null, `researcher` \| `analyst` \| `visualizer` \| `validator` |
| `stage_code` | text | not null, машинный код этапа (например `data_search`, `ndvi`, `change_zones`, `validation`) |
| `title` | text | not null — краткое название для UI |
| `description` | text | not null — описание выполняемой работы (reasoning для live-прогресса, FR-010) |
| `status` | text | not null, `running` \| `completed` \| `failed` \| `skipped` |
| `details` | jsonb | параметры, счётчики, ссылки на инструменты и артефакты |
| `started_at`, `finished_at` | timestamptz | `finished_at` null, пока этап выполняется |

Индексы: `(run_id, seq)` UNIQUE, `(run_id, status)`.
Ограничение: `description` не пустое — этап без объяснения не допускается (Принцип III).

## 6. `validation_rejection`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `run_id` | uuid FK → `analysis_run.id` | not null, on delete cascade |
| `attempt_no` | smallint | not null — 1 = первое отклонение, 2 = отклонение после переработки |
| `category` | text | not null, `analysis_error` \| `data_quality` (FR-007) |
| `reasons` | jsonb | not null — список причин отклонения (не пустой) |
| `fixes_applied` | jsonb | перечень исправлений второй итерации (заполняется для `attempt_no = 1`) |
| `created_at` | timestamptz | not null |

Индексы: `(run_id, attempt_no)` UNIQUE. Ограничение: при `category = 'data_quality'` переработка не
выполняется, поэтому `fixes_applied` остаётся пустым, а `analysis_run.attempt_count = 0`.

## 7. `settings_profile`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `settings_hash` | text PK | sha256 канонизированных значений настроек анализа (R16) |
| `name` | text | not null — человекочитаемое имя профиля |
| `values` | jsonb | not null — порог NDVI, минимальная площадь зоны, порог облачности, окно композита, top-k RAG и т. п. |
| `created_at` | timestamptz | not null |

Профиль неизменяем: изменение значений даёт новый `settings_hash` (воспроизводимость, Принцип II).

## 8. `geocode_cache`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `query_normalized` | text | not null — нормализованное название территории |
| `provider` | text | not null — источник геокодирования (Nominatim/OSM) |
| `payload` | jsonb | not null — сырой ответ провайдера (провенанс границы) |
| `geometry` | geometry(Geometry,4326) | граница результата, если провайдер её вернул |
| `fetched_at`, `expires_at` | timestamptz | not null; кэш снижает частоту обращений (троттлинг, R5) |

Индексы: `(query_normalized, provider)` UNIQUE, GiST(`geometry`).

## 9. `area_of_interest`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `name` | text | not null — из текста запроса; при неудаче извлечения «Область N» (FR-002) |
| `name_source` | text | not null, `query_text` \| `generated` |
| `aoi_type` | text | not null, `admin` \| `poi` \| `buffer` |
| `geocode_cache_id` | uuid FK → `geocode_cache.id` | null, если граница передана пользователем вручную (в этой фиче — нет) |
| `boundary` | geometry(Geometry,4326) | not null; CHECK `ST_SRID(boundary) = 4326`, CHECK `ST_IsValid(boundary)` |
| `validity_fixed` | bool | not null default false — была применена `ST_MakeValid` (Принцип IV) |
| `buffer_m` | int | null — буфер вокруг POI (по умолчанию 1000 м, строится `ST_Buffer`) |
| `area_km2` | numeric(12,4) | not null — считает PostGIS; используется для предупреждения о времени обработки (> ~300 км²) |
| `created_at` | timestamptz | not null |

Индексы: GiST(`boundary`), `(name)`.
Ограничение: при `aoi_type = 'buffer'` поле `buffer_m` обязательно.

## 10. `observation_dataset`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `source` | text | not null — `earth_search` \| `planetary_computer` \| `copernicus` (R1) |
| `collection` | text | not null — `sentinel-2-l2a` \| `landsat-c2-l2` |
| `sensor` | text | not null — `MSI` \| `OLI/TIRS` |
| `scene_id` | text | not null — идентификатор сцены в каталоге |
| `acquired_at` | timestamptz | not null — дата съёмки (указывается в объяснении, FR-009) |
| `cloud_cover` | numeric(5,2) | not null, CHECK `0 ≤ cloud_cover ≤ 100`; пригодными считаются сцены ≤20 % |
| `resolution_m` | numeric(6,2) | not null — 10 / 20 / 30 м |
| `extent` | geometry(Polygon,4326) | not null — охват сцены; CHECK SRID = 4326 |
| `asset_url` | text | not null — ссылка на COG (читается оконно, R1) |
| `checksum` | text | контрольная сумма/версия ассета (провенанс, Принцип II) |
| `is_accepted` | bool | not null — попала ли сцена в композит |
| `rejection_reason` | text | null — например «облачность > 20 %» |

Индексы: `(collection, acquired_at)`, UNIQUE(`source`, `collection`, `scene_id`), GiST(`extent`).

## 11. `run_dataset`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `run_id` | uuid FK → `analysis_run.id` | not null, on delete cascade |
| `dataset_id` | uuid FK → `observation_dataset.id` | not null |
| `role` | text | not null, `start_period` \| `end_period` — к какому концу периода относится сцена |

PK(`run_id`, `dataset_id`, `role`). Совокупность записей образует провенанс результата (FR-004, SC-004).

## 12. `analysis_result`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `run_id` | uuid FK → `analysis_run.id` | not null, UNIQUE — результат принадлежит ровно одному запуску |
| `params_hash` | text | not null, UNIQUE — один результат на набор параметров (FR-019, R16) |
| `mode` | text | not null, `single` \| `combined` — одиночный или «дополненный» результат (FR-012) |
| `territories_count` | smallint | not null, CHECK `between 1 and 5` — лимит территорий (FR-012) |
| `has_overlap` | bool | not null — признак пересечения территорий (решение Q4) |
| `total_area_km2` | jsonb | суммарные площади по классам с обязательной пометкой «сумма по территориям (с возможным пересечением)» |
| `summary_text` | text | not null — краткий вывод |
| `explanation_text` | text | not null — объяснение для неспециалиста (FR-009) |
| `citations` | jsonb | not null — ссылки на фрагменты RAG (документ, чанк, источник, версия индекса) |
| `sources_summary` | jsonb | not null — перечень источников и дат съёмки (FR-004) |
| `crs` | text | not null, значение по умолчанию `EPSG:4326` (FR-017) |
| `validation_outcome` | text | not null, `passed` \| `failed` — итог проверки (FR-007) |
| `created_at` | timestamptz | not null |

Индексы: `(params_hash)` UNIQUE, `(created_at desc)`.

## 13. `result_territory`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `result_id` | uuid FK → `analysis_result.id` | not null, on delete cascade |
| `aoi_id` | uuid FK → `area_of_interest.id` | not null |
| `position` | smallint | not null, CHECK `between 1 and 5` — порядок добавления (FR-012) |
| `territory_name` | text | not null — имя из запроса или «Область N»; попадает в GeoJSON и PDF (FR-014) |
| `retained_area_km2`, `lost_area_km2`, `new_area_km2` | numeric(12,4) | not null, default 0 — статистика по территории (PostGIS) |
| `source_run_id` | uuid FK → `analysis_run.id` | not null — запуск, в котором территория была проанализирована (провенанс при накоплении) |

Индексы: UNIQUE(`result_id`, `position`), `(aoi_id)`.
Слои территорий хранятся **раздельно**: агрегация геометрий не выполняется (решение Q4).

## 14. `change_zone`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `result_territory_id` | uuid FK → `result_territory.id` | not null, on delete cascade |
| `change_class` | text | not null, `retained` \| `lost` \| `new` (FR-006) |
| `geom` | geometry(MultiPolygon,4326) | not null; CHECK `ST_SRID(geom) = 4326`, CHECK `ST_IsValid(geom)` |
| `area_m2` | numeric(18,2) | not null — считается `ST_Area` (в метрической проекции внутри PostGIS) |
| `area_km2` | numeric(12,4) | not null — для UI и экспорта |
| `period_start`, `period_end` | date | not null — границы периода сравнения |
| `cleaned` | bool | not null — применены морфологическая очистка и фильтр минимальной площади (1000 м², R4) |
| `created_at` | timestamptz | not null |

Индексы: GiST(`geom`), `(result_territory_id, change_class)`.

## 15. `artifact`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `run_id` | uuid FK → `analysis_run.id` | not null, on delete cascade |
| `kind` | text | not null: `preview_png`, `final_geotiff`, `geojson`, `pdf`, `raw_scene`, `intermediate_ndvi`, `intermediate_classified` |
| `path` | text | not null — путь в `ArtifactStore` |
| `size_bytes` | bigint | not null |
| `sha256` | text | not null — целостность и воспроизводимость |
| `is_heavy` | bool | not null — `size_bytes > 100 МБ` (R12) |
| `retention_until` | timestamptz | null = бессрочно; заполняется только для `raw_scene` и `intermediate_*` |
| `purge_notice_at` | timestamptz | null; за 7 дней до планового удаления (FR-023) |
| `is_deleted` | bool | not null default false |
| `created_at` | timestamptz | not null |

Индексы: `(run_id, kind)`, частичный по `(retention_until) where is_heavy and not is_deleted`.
Ограничение: `CHECK (kind in ('raw_scene','intermediate_ndvi','intermediate_classified') OR retention_until IS NULL)` —
бессрочные виды артефактов не могут иметь срок хранения (FR-022).

## 16. `artifact_deletion_log`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `run_id` | uuid FK → `analysis_run.id` | not null |
| `artifact_id` | uuid FK → `artifact.id` | not null |
| `kind` | text | not null — что удалено |
| `size_bytes`, `freed_bytes` | bigint | not null — сколько освобождено |
| `reason` | text | not null — причина удаления (истёк `retention_until`, ручная очистка) |
| `task_name` | text | not null — имя задачи очистки |
| `deleted_at` | timestamptz | not null |

Индексы: `(deleted_at desc)`, `(run_id)`. Журнал обязателен для каждого удаления (FR-023, SC-013).

## 17. `knowledge_document`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `title`, `source` | text | not null — название и источник (норма, методика, история работ) |
| `doc_version` | text | not null — версия документа |
| `uri` | text | not null — адрес или путь исходника |
| `checksum` | text | not null UNIQUE — контроль целостности корпуса |
| `indexed_at` | timestamptz | not null — дата индексации |

## 18. `knowledge_chunk`

| Поле | Тип | Ограничения / смысл |
|---|---|---|
| `id` | uuid PK | — |
| `document_id` | uuid FK → `knowledge_document.id` | not null, on delete cascade |
| `ordinal` | int | not null — порядковый номер чанка в документе |
| `content` | text | not null — 800 символов с перекрытием 100 (R8) |
| `embedding` | vector(1536) | not null — эмбеддинги через `LLMProvider.embeddings` (Принцип VI) |
| `token_count` | int | not null |

Индексы: UNIQUE(`document_id`, `ordinal`), HNSW/IVFFlat по `embedding` (косинусная метрика).

## Переходы состояний `analysis_run`

```text
ожидает ──(worker max_jobs=1 берёт задачу)──► в обработке ──► завершён
                                                    │
                                                    ├──► не прошёл проверку
                                                    │    (категория data_quality
                                                    │     ИЛИ повторное отклонение после переработки)
                                                    ├──► данные недоступны
                                                    │    (нет пригодных сцен и альтернатива не выбрана)
                                                    └──► ошибка
                                                         (сбой инструмента, БД, LLM или LangFuse)
```

Правила переходов:

- статусы терминальны и взаимоисключающи (FR-020); выход из терминального состояния невозможен;
- внутри «в обработке» допускается ровно один цикл переработки: `attempt_count` 0 → 1, после чего
  решение валидатора окончательно (FR-007);
- переработка запускается только при `rejection_category = 'analysis_error'`; при `data_quality`
  статус «не прошёл проверку» выставляется сразу;
- каждый переход фиксируется в `run_stage` и дублируется в LangFuse span (Принцип III);
- результаты «завершён» и «не прошёл проверку» доступны для просмотра и экспорта одинаково (SC-011).

## Сводка правил валидации

- Геометрии: `ST_SRID = 4326`, `ST_IsValid`; при невалидности — `ST_MakeValid` и флаг `validity_fixed` (Принцип IV).
- Ввод: `query.text` 3…1000 символов, язык `ru`/`en`; неизвестные поля отвергаются (Pydantic v2, Принцип IX).
- Территории: 1…5 на результат; при попытке добавить шестую — отказ и предложение создать новый запрос (FR-012).
- Период: ≤10 лет, дата съёмки не в будущем; при отсутствии пригодных сцен — сценарий альтернатив (FR-015).
- Сцены: облачность ≤20 %; пригодность фиксируется полями `is_accepted` / `rejection_reason` (FR-003, FR-004).
- Зоны изменений: валидная геометрия, минимальная площадь 1000 м², флаг `cleaned` (R4).
- Артефакты: `is_heavy = size_bytes > 100 МБ`; `retention_until` только для `raw_scene` и `intermediate_*` (FR-022).
- Очистка: `is_reference` и `is_pinned` исключены полностью; уведомление за 7 дней; обязательный журнал (FR-023, SC-013).
- Идемпотентность: уникальность `analysis_result.params_hash`; повторный запуск создаёт новый `analysis_run`
  со ссылкой `reused_result_id` (FR-019).
- Трассировка: `analysis_run.trace_id` обязателен; при недоступности LangFuse запуск завершается статусом «ошибка» (Принцип III).

## Что моделью сознательно не предусмотрено

- загрузка пользовательских данных (снимки, векторы, таблицы) — Out of Scope спецификации;
- мультитенантность, роли и права доступа — Out of Scope (аутентификация вынесена в отдельную фичу);
- клиентские состояния мобильных приложений — Out of Scope (только Web-версия Flutter).