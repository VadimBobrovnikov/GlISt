"""Тесты фикстур тестового контура GlISt (задача T008).

Проверяют гарантии, на которые опираются интеграционные и контрактные тесты:
офлайн-окружение, детерминизм мока LLM, журнал мок-LangFuse и изоляцию
PostGIS-сессии (Принципы II, III, VI, VIII).
"""

import os
from typing import Any

import langfuse
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MockLangfuse, MockLLMProvider


def test_seed_fixture(seed: int) -> None:
    assert seed == 42


def test_autouse_testing_env() -> None:
    assert os.environ["GLIST_TESTING"] == "1"
    assert os.environ["LANGFUSE_HOST"] == "http://127.0.0.1:1999"
    assert "OPENAI_API_KEY" not in os.environ


async def test_mock_llm_deterministic(mock_llm: MockLLMProvider) -> None:
    first = await mock_llm.generate("проанализируй изменения")
    second = await mock_llm.generate("проанализируй изменения")
    assert first == second
    assert first.startswith("MOCK_OK[")


async def test_mock_llm_scenario_directive(mock_llm: MockLLMProvider) -> None:
    out = await mock_llm.generate("[[scenario:analysis_error]] сделай анализ")
    assert out.startswith("MOCK_REJECT[analysis_error]")
    last = mock_llm.calls[-1]
    assert last["scenario"] == "analysis_error"
    assert "[[scenario" not in last["prompt"]


async def test_mock_llm_scenario_attribute(mock_llm: MockLLMProvider) -> None:
    mock_llm.scenario = "data_quality"
    out = await mock_llm.generate("обычный промпт")
    assert out.startswith("MOCK_REJECT[data_quality]")


async def test_mock_llm_stream_matches_generate(mock_llm: MockLLMProvider) -> None:
    chunks = [chunk async for chunk in mock_llm.stream("привет")]
    assert "".join(chunks) == await mock_llm.generate("привет")


async def test_mock_llm_embeddings_deterministic(mock_llm: MockLLMProvider) -> None:
    first = (await mock_llm.embeddings(["текст"]))[0]
    second = (await mock_llm.embeddings(["текст"]))[0]
    assert first == second
    assert len(first) == 1536
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_mock_langfuse_records(mock_langfuse: MockLangfuse) -> None:
    client: Any = langfuse.Langfuse(public_key="pk-test", secret_key="sk-test")
    observation: Any = client.start_observation(name="node.analyst")
    with observation:
        pass
    client.flush()
    assert [call["name"] for call in mock_langfuse.calls] == ["node.analyst", "flush"]


def test_mock_langfuse_observe_passthrough(mock_langfuse: MockLangfuse) -> None:
    @langfuse.observe
    def sample() -> int:
        return 7

    assert sample() == 7
    assert mock_langfuse.calls == []  # observe-декоратор мока не пишет в журнал


async def test_db_postgis_available(db_session: AsyncSession) -> None:
    version = await db_session.scalar(text("SELECT PostGIS_Version()"))
    assert version
    rows = await db_session.execute(text("SELECT extname FROM pg_extension"))
    assert {"postgis", "vector"} <= set(rows.scalars())


async def test_db_write_visible(db_session: AsyncSession) -> None:
    await db_session.execute(text("CREATE TABLE t008_smoke (id integer PRIMARY KEY)"))
    await db_session.execute(text("INSERT INTO t008_smoke VALUES (1)"))
    await db_session.commit()
    assert await db_session.scalar(text("SELECT count(*) FROM t008_smoke")) == 1


async def test_db_isolation_rolled_back(db_session: AsyncSession) -> None:
    with pytest.raises(ProgrammingError):
        await db_session.execute(text("SELECT * FROM t008_smoke"))
