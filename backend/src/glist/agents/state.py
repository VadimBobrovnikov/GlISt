"""Общее состояние графа LangGraph (задача T030).

Описывает общий AgentState, который передаётся между всеми узлами графа
(Researcher, Analyst, Visualizer, Validator, Finalize).

Поля соответствуют разделу «Состояние графа» спецификации мультиагентной системы.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict):
    """Общее состояние, передаваемое между узлами графа LangGraph.

    Все агенты читают и пишут в это состояние. Поле ``messages``
   annotated с ``operator.add`` — сообщения от разных агентов накапливаются
    (не перезаписываются).
    """

    run_id: str
    query_id: str
    query_text: str
    language: str
    mode: str

    next_agent: str
    attempt_count: int

    context: dict[str, Any]
    datasets: dict[str, Any]

    analysis_result: dict[str, Any] | None
    visualization: dict[str, Any] | None
    validation: dict[str, Any] | None

    errors: list[str]

    messages: Annotated[list[Any], operator.add]