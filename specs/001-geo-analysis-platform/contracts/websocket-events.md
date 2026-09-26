# Контракт WebSocket-событий: live-прогресс анализа

**Feature**: `001-geo-analysis-platform` | **Date**: 2026-09-26 | **Plan**: [../plan.md](../plan.md) | **Research**: [../research.md](../research.md) (R9)

Канал: `ws://<host>/ws/runs/{runId}` — один канал на один запуск. Источник событий — callback'и
LangGraph, публикуемые в Redis pub/sub и транслируемые FastAPI. Каждое событие дополнительно
фиксируется как span в LangFuse (Принцип III конституции).

## Формат сообщения

```json
{
  "event": "run.stage.started",
  "runId": "8f1c…",
  "seq": 3,
  "attemptNo": 0,
  "agent": "analyst",
  "occurredAt": "2026-09-26T10:15:03.512Z",
  "payload": { "stageCode": "ndvi", "title": "Расчёт NDVI", "description": "Считаю NDVI по каналам B04/B08" }
}
```

Обязательные поля: `event`, `runId`, `occurredAt`. Остальные зависят от типа события.

## Типы событий

| Событие | Когда отправляется | Полезная нагрузка `payload` |
|---|---|---|
| `run.queued` | Запуск поставлен в очередь (FR-021) | `position` — позиция в очереди |
| `run.started` | Worker начал обработку | `traceId` — идентификатор трассы LangFuse |
| `run.stage.started` | Этап агента начался (FR-010) | `stageCode`, `title`, `description` — краткое описание работы агента |
| `run.stage.completed` | Этап завершён | `stageCode`, `durationMs`, счётчики этапа |
| `run.stage.failed` | Этап завершился ошибкой | `stageCode`, `reason` (без внутренних деталей и стектрейсов) |
| `run.validation.rejected` | Валидатор отклонил результат (FR-007) | `attemptNo` (1 или 2), `category` (`analysis_error` \| `data_quality`), `reasons` (непустой список) |
| `run.rework.started` | Началась переработка (не более одной) | `attemptNo` = 1, `fixesPlanned` — что планируется исправить |
| `run.data.unavailable` | Пригодных данных нет → альтернативы (FR-015) | `reason`, `options` (не менее трёх альтернатив) |
| `run.completed` | Запуск завершён успешно | `resultId`, `territoriesCount`, `areasPurged` |
| `run.failed` | Запуск завершён ошибкой | `statusReason` (человекочитаемо) |
| `artifact.ready` | Артефакт сформирован (превью, GeoJSON, PDF) | `artifactId`, `kind`, `sizeBytes` |
| `run.retention.notice` | Предстоит очистка тяжёлых артефактов (FR-023) | `purgeAt` — дата планового удаления (за 7 дней) |

## Правила

1. Первое событие `run.queued` или `run.stage.started` отправляется не позднее **5 секунд** после
   отправки запроса (SC-007).
2. Обновления во время активной работы — не реже одного раза в 5 секунд (SC-007); события
   `run.stage.*` обязательны для каждого этапа.
3. При обрыве соединения клиент не теряет прогресс: он дозапрашивает `GET /api/v1/runs/{runId}/stages`
   и продолжает слушать канал (edge case «обработка прервана»).
4. Терминальные события (`run.completed`, `run.failed`, `run.data.unavailable`) отправляются один раз;
   после них сервер закрывает соединение штатным закрытием.
5. Нагрузка `payload` не должна содержать секретов, внутренних путей файловой системы и SQL (Принцип IX).
6. Порядок событий гарантируется полем `seq` в пределах одного запуска.

## Соответствие требованиям

| Требование | События контракта |
|---|---|
| FR-010 (поэтапный прогресс с описанием) | `run.stage.started`, `run.stage.completed` |
| FR-007 (отклонение валидатором и переработка) | `run.validation.rejected`, `run.rework.started` |
| FR-015 (альтернативы при отсутствии данных) | `run.data.unavailable` |
| FR-021, SC-012 (очередь и автостарт) | `run.queued`, `run.started` |
| SC-007 (первый этап ≤5 с, обновления ≤5 с) | правило 1, правило 2 |
| FR-023, SC-013 (уведомление за 7 дней) | `run.retention.notice` |