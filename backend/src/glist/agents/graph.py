"""Скелет графа LangGraph (задача T031).

Создаёт мультиагентный граф с узлами Researcher, Analyst, Visualizer,
Validator и Finalize. Граф компилируется и готов к запуску.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from glist.agents.state import AgentState


async def researcher_node(state: AgentState) -> dict[str, Any]:
    """Узел Researcher: ищет данные и возвращает найденную информацию."""
    return {
        "next_agent": "analyst",
        "messages": ["Researcher: данные найдены"],
    }


async def analyst_node(state: AgentState) -> dict[str, Any]:
    """Узел Analyst: анализирует данные и готовит результаты."""
    return {
        "next_agent": "visualizer",
        "messages": ["Analyst: анализ завершён"],
    }


async def visualizer_node(state: AgentState) -> dict[str, Any]:
    """Узел Visualizer: создаёт визуализации результатов анализа."""
    return {
        "next_agent": "validator",
        "messages": ["Visualizer: визуализации готовы"],
    }


async def validator_node(state: AgentState) -> dict[str, Any]:
    """Узел Validator: проверяет результаты и возвращает outcome."""
    return {
        "next_agent": "finalize",
        "validation": {"outcome": "passed"},
        "messages": ["Validator: пройдена"],
    }


async def finalize_node(state: AgentState) -> dict[str, Any]:
    """Узел Finalize: финализирует анализ и возвращает результат."""
    return {
        "messages": ["Finalize: завершено"],
    }


def route_after_validator(state: AgentState) -> str:
    """Условный переход после узла Validator.

    Если validation.outcome == "failed" и attempt_count < 1 — возвращает
    "analyst" для переработки. Иначе — "finalize".
    """
    validation = state.get("validation", {}) or {}
    attempt_count = state.get("attempt_count", 0)

    if validation.get("outcome") == "failed" and attempt_count < 1:
        return "analyst"
    return "finalize"


def build_graph() -> Any:
    """Строит и компилирует мультиагентный граф LangGraph.

    Returns:
        Скомпилированный граф, готовый к запуску.
    """
    graph = StateGraph(AgentState)

    graph.add_node("researcher", researcher_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("visualizer", visualizer_node)
    graph.add_node("validator", validator_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("researcher")

    graph.add_edge("researcher", "analyst")
    graph.add_edge("analyst", "visualizer")
    graph.add_edge("visualizer", "validator")

    graph.add_conditional_edges(
        "validator",
        route_after_validator,
        {
            "analyst": "analyst",
            "finalize": "finalize",
        },
    )

    graph.add_edge("finalize", END)

    return graph.compile()