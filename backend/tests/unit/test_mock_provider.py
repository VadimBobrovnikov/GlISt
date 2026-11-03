"""Unit-тесты MockLLMProvider (задача T023).

Проверяют детерминизм ответов, сценарии валидатора (success / analysis_error /
data_quality), работу stream и embeddings.
"""

from __future__ import annotations

import math

import pytest

from glist.llm.mock_provider import MockLLMProvider


@pytest.mark.asyncio
async def test_generate_deterministic() -> None:
    """Одинаковый (seed, prompt) даёт одинаковый ответ."""
    p1 = MockLLMProvider(seed=42)
    p2 = MockLLMProvider(seed=42)
    r1 = await p1.generate("привет, мир")
    r2 = await p2.generate("привет, мир")
    assert r1 == r2
    assert r1.startswith("MOCK_OK[")


@pytest.mark.asyncio
async def test_generate_different_seed_different_result() -> None:
    """Разные сиды дают разные ответы."""
    p1 = MockLLMProvider(seed=1)
    p2 = MockLLMProvider(seed=2)
    r1 = await p1.generate("привет")
    r2 = await p2.generate("привет")
    assert r1 != r2


@pytest.mark.asyncio
async def test_generate_scenario_attribute() -> None:
    """Атрибут scenario переключает ответ."""
    p = MockLLMProvider(seed=42, scenario="analysis_error")
    r = await p.generate("любой промпт")
    assert r == "MOCK_REJECT[analysis_error]: расчётная площадь противоречит исходным данным"


@pytest.mark.asyncio
async def test_generate_scenario_data_quality() -> None:
    """Сценарий data_quality."""
    p = MockLLMProvider(seed=42, scenario="data_quality")
    r = await p.generate("любой промпт")
    assert r == "MOCK_REJECT[data_quality]: данные не покрывают запрошенный период анализа"


@pytest.mark.asyncio
async def test_generate_scenario_directive() -> None:
    """Директива [[scenario:…]] в промпте переключает ответ."""
    p = MockLLMProvider(seed=42, scenario="success")
    r = await p.generate("[[scenario:data_quality]] проверь данные")
    assert r == "MOCK_REJECT[data_quality]: данные не покрывают запрошенный период анализа"
    # Директива удалена из записи вызова
    assert p.calls[0]["prompt"] == " проверь данные"


@pytest.mark.asyncio
async def test_stream_deterministic() -> None:
    """Stream даёт те же куски для одинакового (seed, prompt)."""
    p1 = MockLLMProvider(seed=42)
    p2 = MockLLMProvider(seed=42)
    chunks1 = [c async for c in p1.stream("тест")]
    chunks2 = [c async for c in p2.stream("тест")]
    assert chunks1 == chunks2
    assert "".join(chunks1).startswith("MOCK_OK[")


@pytest.mark.asyncio
async def test_stream_scenario() -> None:
    """Stream с scénarios возвращает соответствующий ответ."""
    p = MockLLMProvider(seed=42, scenario="analysis_error")
    chunks = [c async for c in p.stream("любой")]
    assert (
        "".join(chunks)
        == "MOCK_REJECT[analysis_error]: расчётная площадь противоречит исходным данным"
    )


@pytest.mark.asyncio
async def test_embeddings_deterministic() -> None:
    """Embeddings детерминированы по (seed, текст)."""
    p1 = MockLLMProvider(seed=42)
    p2 = MockLLMProvider(seed=42)
    v1 = await p1.embeddings(["текст один", "текст два"])
    v2 = await p2.embeddings(["текст один", "текст два"])
    assert v1 == v2
    assert len(v1) == 2
    assert all(len(v) == 1536 for v in v1)


@pytest.mark.asyncio
async def test_embeddings_normalized() -> None:
    """Векторы эмбеддингов нормализованы (длина = 1)."""
    p = MockLLMProvider(seed=42)
    vectors = await p.embeddings(["текст"])

    vec = vectors[0]
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_calls_recorded() -> None:
    """Все вызовы записываются в calls для assert в тестах."""
    p = MockLLMProvider(seed=42)
    await p.generate("prompt 1", temperature=0.7)
    async for _ in p.stream("prompt 2"):
        pass
    await p.embeddings(["текст"])

    assert len(p.calls) == 3
    assert p.calls[0]["kind"] == "generate"
    assert p.calls[0]["prompt"] == "prompt 1"
    assert p.calls[0]["params"] == {"temperature": 0.7}
    assert p.calls[1]["kind"] == "stream"
    assert p.calls[2]["kind"] == "embeddings"
    assert p.calls[2]["count"] == 1
