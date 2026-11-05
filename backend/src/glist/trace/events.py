"""Публикация событий прогресса через Redis pub/sub (задача T026).

События публикуются строго по контракту
specs/001-geo-analysis-platform/contracts/websocket-events.md.

Каждое событие содержит обязательные поля event, runId, occurredAt
и уникальный seq (порядковый номер в рамках одного запуска).
"""

import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from glist.config import get_settings


class EventPublisher:
    """Публикатор событий прогресса в Redis pub/sub.

    Каждое событие содержит обязательные поля event, runId, occurredAt
    и уникальный seq (порядковый номер в рамках одного запуска).
    """

    def __init__(self, redis: aioredis.Redis | None = None) -> None:
        self._redis = redis
        self._seq: dict[str, int] = {}

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(  # type: ignore[no-untyped-call]
                settings.redis_url.get_secret_value(),
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def _next_seq(self, run_id: str) -> int:
        self._seq[run_id] = self._seq.get(run_id, 0) + 1
        return self._seq[run_id]

    async def publish(
        self,
        run_id: str,
        event: str,
        payload: dict[str, Any] | None = None,
        attempt_no: int | None = None,
        agent: str | None = None,
        stage_code: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        """Публикует событие в канал Redis run:{run_id}.

        Args:
            run_id: Идентификатор запуска.
            event: Тип события (run.queued, run.stage.started, ...).
            payload: Полезная нагрузка без секретов и внутренних деталей.
            attempt_no: Номер попытки (для run.validation.rejected и т.д.).
            agent: Имя агента (для run.stage.* событий).
            stage_code: Код этапа (для run.stage.* событий).
            duration_ms: Длительность этапа в мс.

        Returns:
            Порядковый номер события (seq).

        Raises:
            redis.RedisError: если публикация не удалась.
        """
        seq = self._next_seq(run_id)
        event_dict: dict[str, Any] = {
            "event": event,
            "runId": run_id,
            "seq": seq,
            "occurredAt": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }

        if attempt_no is not None:
            event_dict["attemptNo"] = attempt_no
        if agent is not None:
            event_dict["agent"] = agent

        full_payload: dict[str, Any] = {}
        if stage_code is not None:
            full_payload["stageCode"] = stage_code
        if duration_ms is not None:
            full_payload["durationMs"] = duration_ms
        if payload:
            full_payload.update(payload)

        if full_payload:
            event_dict["payload"] = full_payload

        redis = await self._get_redis()
        channel = f"run:{run_id}"
        await redis.publish(channel, json.dumps(event_dict, ensure_ascii=False))
        return seq

    async def close(self) -> None:
        """Закрывает соединение с Redis."""
        if self._redis is not None:
            await self._redis.close()


# Глобальный экземпляр
_publisher: EventPublisher | None = None


def get_publisher() -> EventPublisher:
    """Возвращает глобальный экземпляр EventPublisher."""
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher
