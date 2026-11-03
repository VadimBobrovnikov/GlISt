"""Unit-тесты публикации событий прогресса (задача T026).

Проверяют формат событий по контракту websocket-events.md: обязательные поля
event/runId/occurredAt, уникальный seq, публикацию в Redis pub/sub.
Используется FakeRedis — реальный Redis не требуется.
"""

import json
import pytest

from typing import Any

from glist.trace.events import EventPublisher, get_publisher


class FakeRedis:
    """Фейковый Redis для тестов: записывает опубликованные сообщения."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return len(self.published)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_publish_basic_event() -> None:
    """Базовое событие содержит event, runId, seq, occurredAt."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    seq = await publisher.publish(
        run_id="run-123",
        event="run.queued",
        payload={"position": 1},
    )

    assert seq == 1
    assert len(fake.published) == 1
    channel, message = fake.published[0]
    assert channel == "run:run-123"

    data = json.loads(message)
    assert data["event"] == "run.queued"
    assert data["runId"] == "run-123"
    assert data["seq"] == 1
    assert "occurredAt" in data
    assert data["occurredAt"].endswith("Z")
    assert data["payload"] == {"position": 1}


@pytest.mark.asyncio
async def test_publish_seq_increments() -> None:
    """seq увеличивается для каждого события в рамках одного запуска."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    seq1 = await publisher.publish(run_id="run-123", event="run.stage.started")
    seq2 = await publisher.publish(run_id="run-123", event="run.stage.completed")
    seq3 = await publisher.publish(run_id="run-123", event="run.stage.failed")

    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 3


@pytest.mark.asyncio
async def test_publish_seq_per_run() -> None:
    """seq independientes для разных запусков."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(run_id="run-1", event="run.queued")
    seq_run2 = await publisher.publish(run_id="run-2", event="run.queued")
    seq_run1_again = await publisher.publish(run_id="run-1", event="run.started")

    assert seq_run2 == 1  # первый для run-2
    assert seq_run1_again == 2  # второй для run-1


@pytest.mark.asyncio
async def test_publish_stage_event_with_agent_and_stage_code() -> None:
    """Событие этапа содержит agent и stageCode в payload."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(
        run_id="run-123",
        event="run.stage.started",
        agent="analyst",
        stage_code="ndvi",
        payload={"title": "Расчёт NDVI", "description": "Считаю NDVI"},
        duration_ms=1500,
    )

    data = json.loads(fake.published[0][1])
    assert data["event"] == "run.stage.started"
    assert data["agent"] == "analyst"
    assert data["payload"]["stageCode"] == "ndvi"
    assert data["payload"]["durationMs"] == 1500
    assert data["payload"]["title"] == "Расчёт NDVI"
    assert data["payload"]["description"] == "Считаю NDVI"


@pytest.mark.asyncio
async def test_publish_validation_rejected() -> None:
    """Событие отклонения валидатора содержит attemptNo и category."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(
        run_id="run-123",
        event="run.validation.rejected",
        attempt_no=1,
        payload={
            "category": "analysis_error",
            "reasons": ["некорректная геометрия"],
        },
    )

    data = json.loads(fake.published[0][1])
    assert data["event"] == "run.validation.rejected"
    assert data["attemptNo"] == 1
    assert data["payload"]["category"] == "analysis_error"
    assert data["payload"]["reasons"] == ["некорректная геометрия"]


@pytest.mark.asyncio
async def test_publish_terminal_event_completed() -> None:
    """Терминальное событие run.completed содержит resultId."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(
        run_id="run-123",
        event="run.completed",
        payload={
            "resultId": "result-456",
            "territoriesCount": 3,
            "areasPurged": 0,
        },
    )

    data = json.loads(fake.published[0][1])
    assert data["event"] == "run.completed"
    assert data["payload"]["resultId"] == "result-456"
    assert data["payload"]["territoriesCount"] == 3


@pytest.mark.asyncio
async def test_publish_no_infrastructure_secrets() -> None:
    """Публикация не добавляет инфраструктурные секреты в сообщение."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(
        run_id="run-123",
        event="run.stage.started",
        payload={"stageCode": "ndvi", "title": "Расчёт NDVI"},
    )

    message = fake.published[0][1]
    data = json.loads(message)
    # Нет полей, которые могли бы раскрыть внутренности инфраструктуры
    assert "redis_url" not in message
    assert "internal" not in data
    assert "connection_string" not in message
    assert "password" not in message.lower()
    
    assert data["payload"]["stageCode"] == "ndvi"


@pytest.mark.asyncio
async def test_publish_channel_pattern() -> None:
    """Канал Redis имеет формат run:{run_id}."""
    fake = FakeRedis()
    publisher = EventPublisher(redis=fake)

    await publisher.publish(run_id="abc-def-123", event="run.queued")

    channel, _ = fake.published[0]
    assert channel == "run:abc-def-123"


def test_get_publisher_singleton() -> None:
    """get_publisher возвращает один и тот же экземпляр."""
    import glist.trace.events as events_module

    events_module._publisher = None

    p1 = get_publisher()
    p2 = get_publisher()
    assert p1 is p2
