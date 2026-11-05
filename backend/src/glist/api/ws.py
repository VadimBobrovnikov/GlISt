"""WebSocket для прогресса анализа (задача T038).

Endpoint:
- ``WS /api/v1/ws/runs/{run_id}`` — подписка на Redis канал ``run:{run_id}``
  через :class:`EventPublisher` и пересылка событий клиенту в JSON.
  Соединение закрывается при терминальном событии.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from glist.api.deps import get_current_user
from glist.trace.events import EventPublisher, get_publisher

router = APIRouter(prefix="/api/v1")

# Терминальные события, при получении которых соединение закрывается.
_TERMINAL_EVENTS = frozenset(
    {"run.completed", "run.failed", "run.validation.rejected"}
)


@router.websocket("/ws/runs/{run_id}")
async def ws_run_progress(  # noqa: B008
    websocket: WebSocket,
    run_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> None:
    """WebSocket-эндпоинт для потока событий прогресса запуска.

    Подписывается на Redis канал ``run:{run_id}`` и пересылает все события
    клиенту в формате JSON. При получении терминального события
    (``run.completed``, ``run.failed``, ``run.validation.rejected``)
    соединение закрывается.

    Args:
        websocket: WebSocket-соединение FastAPI.
        run_id: Идентификатор запуска analysis_run.
        current_user: Текущий пользователь (заглушка аутентификации).
    """
    await websocket.accept()
    publisher: EventPublisher = get_publisher()
    redis = await publisher._get_redis()
    channel = f"run:{run_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                continue
            data: Any = message.get("data")
            if not isinstance(data, str):
                continue
            event_dict = json.loads(data)
            event_type = event_dict.get("event", "")
            await websocket.send_json(event_dict)
            if event_type in _TERMINAL_EVENTS:
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()