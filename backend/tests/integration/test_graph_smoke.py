"""Интеграционный smoke-тест графа LangGraph (задача T041).

Запускает полный граф (Researcher → Analyst → Visualizer → Validator → Finalize)
с ``MockLLMProvider`` и проверяет, что выполнение проходит без ошибок.
"""

from __future__ import annotations

from typing import Any

import pytest

from glist.agents.graph import build_graph
from glist.agents.state import AgentState
from glist.llm.mock_provider import MockLLMProvider


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    """Детерминированный мок LLM-провайдера для smoke-теста."""
    return MockLLMProvider(seed=42, scenario="success")


@pytest.fixture
def initial_state() -> AgentState:
    """Минимальное начальное состояние для прогона графа."""
    return AgentState(
        run_id="smoke-run-1",
        query_id="smoke-query-1",
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


@pytest.mark.asyncio
async def test_graph_smoke_passes(
    mock_llm: MockLLMProvider, initial_state: AgentState
) -> None:
    """Граф проходит от Researcher до Finalize без ошибок."""
    graph = build_graph()
    result: dict[str, Any] = await graph.ainvoke(initial_state)  # type: ignore[arg-type]

    assert result is not None
    assert "errors" in result
    assert result["errors"] == []
    assert any(
        "finalize" in str(m).lower() or "завершено" in str(m).lower()
        for m in result.get("messages", [])
    )