# Контракт общего состояния графа агентов (State)

**Feature**: `001-geo-analysis-platform` | **Date**: 2026-09-26 | **Plan**: [../plan.md](../plan.md) | **Research**: [../research.md](../research.md) (R6, R7)

Реализация: `backend/src/glist/agents/state.py` (Pydantic v2-модели + `TypedDict` для LangGraph).
Инвариант конституции (Принцип V): **агенты общаются только через это состояние** — прямые импорты и
вызовы между модулями агентов запрещены; типизированные ключи обязательны, «мешки» `dict` без схемы
не допускаются.

## Правила состояния

1. Каждый узел получает состояние и возвращает **частичное обновление** (dict новых/изменённых ключей).
2. Списки, дополняемые разными узлами (`stages`, `errors`, `artifacts`, `rag_context`), используют
   reducer `operator.add`; скалярные ключи перезаписываются.
3. В состоянии **никогда не хранятся бинарные данные** (растры, изображения): только ссылки на
   артефакты и метрики — это защищает чекпойнты и трассы от «раздувания».
4. Каждый узел обязан записывать в `stages` запись с непустым `description` (Принцип III).
5. Значения `settings_hash`, `params_hash`, `seed`, `trace_id` заполняются один раз на входе и после
   этого не изменяются (воспроизводимость, Принцип II).

## Поля состояния

| Ключ | Тип | Заполняет | Назначение |
|---|---|---|---|
| `run_id` | `UUID` | оркестратор | Идентификатор запуска; ключ трассировки (FR-016) |
| `query_id` | `UUID` | оркестратор | Связь с запросом пользователя |
| `parent_run_id` | `UUID \| None` | оркестратор | Цепочка уточнений (FR-013) |
| `query_text` | `str` | оркестратор | Исходный вопрос (3…1000 символов) |
| `language` | `Literal["ru","en"]` | researcher | Язык запроса |
| `mode` | `Literal["single","combined"]` | оркестратор | Режим результата: одиночный или «дополнить» (FR-012) |
| `context` | `RunContext` | оркестратор | Унаследованный контекст: `territories[]`, `period`, `analysis_type` — **все** ранее накопленные территории, а не только последняя (SC-009) |
| `territories` | `list[TerritoryRef]` | researcher | Состав территорий (1…5): `name`, `name_source`, `aoi_type`, `boundary_geojson`, `buffer_m`, `area_km2` |
| `period` | `Period` | researcher | `start`, `end` (не более 10 лет, не в будущем) |
| `analysis_type` | `Literal["vegetation_change"]` | researcher | Тип анализа (в этой фиче — изменение растительности) |
| `datasets` | `DatasetSelection` | researcher | `accepted[]`, `rejected[]` — провенанс сцен: источник, дата съёмки, облачность, разрешение (FR-004) |
| `data_alternatives` | `list[Alternative]` | researcher | Альтернативы при отсутствии данных: другой период, другой источник, ближайшая дата (FR-015) |
| `composite_params` | `CompositeParams` | analyst | Окно композита, порог облачности, использованные маски |
| `settings_hash` | `str` | оркестратор | Хэш профиля настроек анализа (`settings_profile`) |
| `params_hash` | `str` | оркестратор | Хэш набора параметров для идемпотентности (FR-019, R16) |
| `seed` | `int` | оркестратор | Seed воспроизводимости (Принцип II) |
| `trace_id` | `str` | оркестратор | Идентификатор трассы LangFuse (Принцип III) |
| `raster_refs` | `RasterRefs` | analyst | Ссылки на артефакты `start_ndvi`, `end_ndvi`, `start_class`, `end_class` (не сами растры) |
| `change_zones` | `list[ChangeZone]` | analyst | Зоны: `territory_position`, `change_class`, `area_km2`, `geometry_geojson`, `zones_count` |
| `territory_stats` | `list[TerritoryStats]` | analyst | Площади по классам на каждую территорию (таблицы PDF и GeoJSON) |
| `aggregate` | `AggregateStats` | analyst | Суммарные площади, флаг `has_overlap` и обязательная пометка «сумма по территориям (с возможным пересечением)» |
| `rag_context` | `list[RagChunk]` | visualizer / validator | Извлечённые фрагменты (документ, чанк, версия, близость) — Принцип VII |
| `explanation` | `Explanation` | visualizer | `summary`, `text` (для неспециалиста), `citations[]`, привязка выводов к территориям |
| `map_layers` | `list[MapLayer]` | visualizer | Слои для клиента: класс, цвет, видимость по умолчанию |
| `preview_ref`, `final_geotiff_ref` | `ArtifactRef \| None` | visualizer | Ссылки на превью-карту и финальный GeoTIFF |
| `validation` | `Validation` | validator | `outcome` (`passed`/`failed`), `attempt_no`, `category` (`analysis_error`/`data_quality`), `reasons[]`, `fixes_applied[]` |
| `attempt_no` | `int` | validator | 0 — первая попытка, 1 — переработка; переход к 1 запрещён дважды (FR-007) |
| `stages` | `list[Stage]` (reducer) | все узлы | Записи этапов: `seq`, `agent`, `stage_code`, `title`, `description`, `status`, timestamps |
| `artifacts` | `list[ArtifactRef]` (reducer) | analyst, visualizer | Артефакты запуска с `kind` и `size_bytes` |
| `errors` | `list[RunError]` (reducer) | все узлы | Неподавленные ошибки: каждое попадание отражается в статусе и трассировке (Принцип III) |
| `status` | `RunStatus` | все узлы | Итоговый статус: `ожидает` / `в обработке` / `завершён` / `не прошёл проверку` / `данные недоступны` / `ошибка` (FR-020) |
| `status_reason` | `str \| None` | validator, оркестратор | Причина терминального статуса |

## Правила маршрутизации в графе

| Условие после `validator` | Переход |
|---|---|
| `validation.outcome == "passed"` | `finalize` → статус `завершён` |
| `validation.outcome == "failed"`, `category == "analysis_error"`, `attempt_no == 0` | `analyst` — ровно одна переработка |
| `validation.outcome == "failed"`, `category == "data_quality"` | `finalize` → `не прошёл проверку` (переработка не запускается) |
| `validation.outcome == "failed"`, `attempt_no == 1` | `finalize` → `не прошёл проверку`; в состоянии сохранены обе группы причин: первое отклонение, `fixes_applied` второй итерации, второе отклонение |
| `data_alternatives` непусто и пользователь не выбрал вариант | `finalize` → `данные недоступны` |
| необработанная ошибка инструмента, БД, LLM или LangFuse | `finalize` → `ошибка` |

## Пример частичного обновления от узла `analyst`

```json
{
  "stages": [
    {
      "seq": 3,
      "agent": "analyst",
      "stageCode": "change_zones",
      "title": "Выделение зон изменений",
      "description": "Сравниваю бинарные классы начала и конца периода, строю зоны и считаю площади в PostGIS",
      "status": "completed"
    }
  ],
  "change_zones": [
    { "territoryPosition": 1, "changeClass": "lost", "areaKm2": 12.41, "zonesCount": 7 }
  ],
  "aggregate": {
    "hasOverlap": false,
    "note": "сумма по территориям (с возможным пересечением)"
  }
}
```

Геометрии в состоянии передаются как GeoJSON и **обязательно** проверяются на валидность и SRID
средствами PostGIS перед сохранением (Принцип IV); площади приходят из PostGIS, а не считаются в Python.

## Тестируемость (Принцип VIII)

- Каждый узел тестируется изолированно: подаётся фикстура состояния, проверяется возвращаемое
  частичное обновление и отсутствие прямых зависимостей от других узлов.
- Интеграционный тест графа прогоняет полный маршрут с мок-провайдером LLM на тестовой БД PostGIS и
  обязан покрывать все ветви: успех, одну переработку, отказ по качеству данных, отсутствие данных и
  техническую ошибку.
- Тест обязан подтверждать: переработка возможна не более одного раза; при повторном отклонении в
  состоянии присутствуют обе группы причин; `stages` содержит непустые `description` для каждого шага.
- Состояние должно оставаться сериализуемым для чекпойнтов LangGraph (проверяется отдельным тестом:
  отсутствие бинарных полей и объектов Rasterio/GeoDataFrame внутри состояния).