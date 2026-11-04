"""Фоновая задача выполнения анализа (задача T033).

Запускает скомпилированный граф LangGraph для заданного run_id,
публикует события в Redis через EventPublisher и обновляет статус в БД.
"""

from __future__ import annotations

import logging
from typing import Any

from glist.agents.graph import build_graph
from glist.agents.state import AgentState
from glist.trace.events import EventPublisher

logger = logging.getLogger("glist.jobs.analyze_run")


async def analyze_run(ctx: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Основная задача ARQ: выполнение графа анализа для запуска.

    Args:
        ctx: Контекст ARQ (содержит redis_settings и т.д.).
        run_id: Идентификатор запуска analysis_run в БД.

    Returns:
        Результат выполнения со статусом и сообщениями графа.
    """
    publisher = EventPublisher()

    try:
        # Публикуем событие о начале работы
        await publisher.publish(
            run_id=run_id,
            event="run.started",
            payload={"run_id": run_id},
        )

        # Инициализируем состояние графа
        initial_state: AgentState = {
            "run_id": run_id,
            "query_id": "",
            "query_text": "",
            "language": "ru",
            "mode": "single",
            "next_agent": "researcher",
            "attempt_count": 0,
            "context": {},
            "datasets": {},
            "analysis_result": None,
            "visualization": None,
            "validation": None,
            "errors": [],
            "messages": [],
        }

        # Запускаем скомпилированный граф
        graph = build_graph()
        result = await graph.ainvoke(initial_state)

        # Публикуем событие о завершении
        await publisher.publish(
            run_id=run_id,
            event="run.completed",
            payload={
                "run_id": run_id,
                "status": "completed",
                "next_agent": result.get("next_agent"),
                "errors": result.get("errors", []),
            },
        )

        return {
            "status": "completed",
            "run_id": run_id,
            "messages": result.get("messages", []),
            "errors": result.get("errors", []),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка выполнения анализа run_id=%s", run_id)

        # Публикуем событие об ошибке
        await publisher.publish(
            run_id=run_id,
            event="run.failed",
            payload={"run_id": run_id, "error": str(exc)},
        )

        return {
            "status": "failed",
            "run_id": run_id,
            "error": str(exc),
        }

    finally:
        await publisher.close()