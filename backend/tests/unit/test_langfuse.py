"""Unit-тесты трассировки LangFuse (задача T025).

Все тесты используют MockLangfuse (из tests/conftest.py) — сетевых вызовов нет.
Проверяют создание трассы, спанов, генераций, flush/shutdown и обработку
ошибок недоступности LangFuse (Принцип III).
"""

from typing import Any

import pytest
from langfuse import Langfuse  # noqa: F401

from glist.trace.langfuse import LangfuseError, LangfuseTracer, get_tracer


def test_create_trace(mock_langfuse: Any, required_env: dict[str, str]) -> None:
    """Создание трассы записывает вызов в журнал мока."""
    tracer = LangfuseTracer()
    trace = tracer.create_trace(
        trace_id="trace-123",
        run_id="run-456",
        name="analysis_run",
        metadata={"agent": "researcher"},
    )
    assert trace is not None
    assert len(mock_langfuse.calls) >= 1
    assert mock_langfuse.calls[0]["name"] == "trace"


@pytest.mark.asyncio
async def test_create_span(mock_langfuse: Any, required_env: dict[str, str]) -> None:
    """Создание спана записывает вызов в журнал мока."""
    tracer = LangfuseTracer()
    tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")
    mock_langfuse.calls.clear()

    span = tracer.create_span(
        trace_id="trace-123",
        span_id="span-1",
        name="researcher_node",
        input={"prompt": "..."},
    )
    assert span is not None
    assert len(mock_langfuse.calls) >= 1
    assert mock_langfuse.calls[0]["name"] == "span"


@pytest.mark.asyncio
async def test_create_generation(mock_langfuse: Any, required_env: dict[str, str]) -> None:
    """Создание генерации LLM записывает вызов в журнал мока."""
    tracer = LangfuseTracer()
    tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")
    mock_langfuse.calls.clear()

    gen = tracer.create_generation(
        trace_id="trace-123",
        span_id="span-1",
        generation_id="gen-1",
        name="llm_call",
        model="gpt-4o-mini",
        input={"prompt": "анализ"},
        output={"result": "ok"},
        usage={"tokens": 100},
    )
    assert gen is not None
    assert len(mock_langfuse.calls) >= 1
    assert mock_langfuse.calls[0]["name"] == "generation"


@pytest.mark.asyncio
async def test_flush_and_shutdown(mock_langfuse: Any, required_env: dict[str, str]) -> None:
    """flush и shutdown записывают вызовы в журнал мока."""
    tracer = LangfuseTracer()
    tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")
    mock_langfuse.calls.clear()

    tracer.flush()
    tracer.shutdown()
    assert len(mock_langfuse.calls) == 2
    assert mock_langfuse.calls[0]["name"] == "flush"
    assert mock_langfuse.calls[1]["name"] == "shutdown"


def test_get_tracer_singleton(mock_langfuse: Any, required_env: dict[str, str]) -> None:
    """get_tracer возвращает синглтон."""
    t1 = get_tracer()
    t2 = get_tracer()
    assert t1 is t2


@pytest.mark.asyncio
async def test_langfuse_unavailable_raises(
    monkeypatch: pytest.MonkeyPatch, required_env: dict[str, str]
) -> None:
    """При недоступности LangFuse выбрасывается LangfuseError (Принцип III)."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("langfuse unreachable")

    import glist.trace.langfuse as trace_module

    trace_module._tracer = None
    monkeypatch.setattr(trace_module.Langfuse, "__init__", _boom, raising=False)

    tracer = LangfuseTracer()
    with pytest.raises(LangfuseError, match="Не удалось инициализировать LangFuse"):
        tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")


@pytest.mark.asyncio
async def test_trace_call_error_raises(
    mock_langfuse: Any, monkeypatch: pytest.MonkeyPatch, required_env: dict[str, str]
) -> None:
    """Ошибка при вызове client.trace выбрасывает LangfuseError."""
    import glist.trace.langfuse as trace_module

    trace_module._tracer = None
    tracer = LangfuseTracer()
    tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")
    mock_langfuse.calls.clear()

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("langfuse error")

    monkeypatch.setattr(tracer._client, "trace", _boom, raising=False)

    with pytest.raises(LangfuseError, match="Ошибка создания трассы LangFuse"):
        tracer.create_trace(trace_id="trace-123", run_id="run-456", name="analysis_run")
