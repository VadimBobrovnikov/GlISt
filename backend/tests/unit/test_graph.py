"""Тестыグラфа LangGraph (задача T031).

Проверяет компиляцию графа и базовый прогон по узлам.
"""

from __future__ import annotations

import pytest

from glist.agents.graph import build_graph, route_after_validator
from glist.agents.state import AgentState


@pytest.fixture
def empty_state() -> AgentState:
    """Минимальное начальное состояние для тестов графа."""
    return AgentState(
        run_id="test-run-1",
        query_id="test-query-1",
        query_text="Покажи площадь района",
        language="ru",
        mode="single",
        next_agent="researcher",
        attempt_count=0,
        context={},
        datasets={},
        analysis_result=None,
        visualization=None,
        validation=None,
        errors=[],
        messages=[],
    )


def test_build_graph_compiles() -> None:
    """Граф успешно компилируется (не возвращает None)."""
    compiled = build_graph()
    assert compiled is not None


def test_route_after_validator_passed(empty_state: AgentState) -> None:
    """При passed outcome возвращается finalize."""
    empty_state["validation"] = {"outcome": "passed"}
    empty_state["attempt_count"] = 0
    result = route_after_validator(empty_state)
    assert result == "finalize"


def test_route_after_validator_failed_retries(empty_state: AgentState) -> None:
    """При failed outcome и attempt_count < 1 возвращается analyst."""
    empty_state["validation"] = {"outcome": "failed"}
    empty_state["attempt_count"] = 0
    result = route_after_validator(empty_state)
    assert result == "analyst"


def test_route_after_validator_failed_max_attempts(empty_state: AgentState) -> None:
    """При failed outcome и attempt_count >= 1 возвращается finalize."""
    empty_state["validation"] = {"outcome": "failed"}
    empty_state["attempt_count"] = 1
    result = route_after_validator(empty_state)
    assert result == "finalize"