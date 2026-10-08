"""Общие фикстуры pytest для GlISt (задача T008).

Интеграционный контур тестов (Принцип VIII):

1. ``postgres_container`` — session-контейнер PostgreSQL + PostGIS + pgvector через
   testcontainers. Образ — локальная сборка ``glist/postgis-pgvector:16-3.6-pgvector``
   (тот же, что в docker-compose.yml; официальные образы не содержат PostGIS и
   pgvector сразу);
   при отсутствии образа в Docker он собирается из ``backend/deploy`` автоматически.
   После старта выполняются проверки ``SELECT PostGIS_Version()`` и
   ``SELECT extname FROM pg_extension``.
2. ``db_session`` — async-сессия (asyncpg) с откатом изменений каждого теста
   (изоляция через savepoint: внешняя транзакция никогда не коммитится).
3. ``mock_llm`` — детерминированный мок LLM-провайдера: async ``generate``/``stream``/
   ``embeddings``, сценарии валидатора ``success``/``analysis_error``/``data_quality``
   (контракт T020/T023, Принцип VI).
4. ``mock_langfuse`` — мок-клиент LangFuse: офлайн-тесты без сети, все вызовы пишутся
   в журнал для проверки факта трассировки (Принцип III, контракт T089).
5. ``client`` — ``httpx.AsyncClient`` поверх ASGI-приложения FastAPI (появится в T036).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import random
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import langfuse
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

DB_IMAGE = "glist/postgis-pgvector:16-3.6-pgvector"
_DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
_EMBEDDING_DIM = 1536  # vector(1536): knowledge_chunk.embedding (data-model.md)
Scenario = Literal["success", "analysis_error", "data_quality"]
_SCENARIO_DIRECTIVE = re.compile(r"\[\[scenario:(success|analysis_error|data_quality)\]\]")

# Фиксированные ответы сценариев валидатора (T023, тесты T088/T089).
_SCENARIO_RESPONSES: dict[Scenario, str] = {
    "analysis_error": (
        "MOCK_REJECT[analysis_error]: расчётная площадь противоречит исходным данным"
    ),
    "data_quality": ("MOCK_REJECT[data_quality]: данные не покрывают запрошенный период анализа"),
}

# Общий журнал вызовов всех мок-клиентов LangFuse прогона: фикстура чистит его
# перед каждым тестом, экземпляры классов пишут сюда же (см. mock_langfuse).
_MOCK_LANGFUSE_CALLS: list[dict[str, Any]] = []


def _ensure_db_image() -> None:
    """Гарантирует наличие локального образа БД стенда.

    testcontainers поднимает локальный образ по имени тега — образ должен уже
    существовать в Docker (или собираться вручную: ``docker compose build postgres``).
    Здесь при отсутствии образа выполняется автосборка из ``backend/deploy``,
    чтобы интеграционные тесты не зависели от предварительных шагов.
    """
    if shutil.which("docker") is None:
        raise RuntimeError("Docker не найден: интеграционные фикстуры T008 требуют Docker-демон")
    probe = subprocess.run(
        ["docker", "image", "inspect", DB_IMAGE],
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return
    build = subprocess.run(
        [
            "docker",
            "build",
            "--quiet",
            "-f",
            str(_DEPLOY_DIR / "postgis-pgvector.Dockerfile"),
            "-t",
            DB_IMAGE,
            str(_DEPLOY_DIR),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Сборка образа {DB_IMAGE} не удалась (docker build: код {build.returncode}).\n"
            f"{build.stderr[-2000:]}"
        )


async def _verify_postgis(database_url: str) -> None:
    """Проверяет готовность БД после старта контейнера (ТЗ задачи T008).

    Образ содержит только бинарники расширений, поэтому расширения создаются
    идемпотентно (те же ``CREATE EXTENSION IF NOT EXISTS`` обязаны быть в
    первой миграции T018), после чего выполняются обе штатные проверки.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        async with engine.connect() as conn:
            postgis_version = await conn.scalar(text("SELECT PostGIS_Version()"))
            extensions = set(
                (await conn.execute(text("SELECT extname FROM pg_extension"))).scalars()
            )
    finally:
        await engine.dispose()
    missing = {"postgis", "vector"} - extensions
    if postgis_version is None or missing:
        raise RuntimeError(
            "Контейнер БД не прошёл проверку PostGIS/pgvector: "
            f"PostGIS_Version()={postgis_version!r}, отсутствуют расширения={sorted(missing)}"
        )


class MockLLMProvider:
    """Мок LLM-провайдера по контракту T020 (async generate/stream/embeddings).

    Детерминированность: ``generate``/``stream`` — чистая функция ``(seed, prompt)``,
    ``embeddings`` — единичный вектор длины 1536 из генератора с сидом ``seed + текст``
    (Принцип II). Сценарий валидатора выбирается атрибутом ``scenario`` или директивой
    ``[[scenario:analysis_error]]`` в промпте (директива из промпта удаляется).
    Все вызовы записываются в ``calls`` для assert в тестах.
    """

    def __init__(self, seed: int = 42, scenario: Scenario = "success") -> None:
        self.seed = seed
        self.scenario = scenario
        self.calls: list[dict[str, Any]] = []

    def _resolve(self, prompt: str) -> tuple[Scenario, str]:
        match = _SCENARIO_DIRECTIVE.search(prompt)
        if match is None:
            return self.scenario, prompt
        scenario = cast(Scenario, match.group(1))
        return scenario, _SCENARIO_DIRECTIVE.sub("", prompt, count=1)

    def _response_for(self, scenario: Scenario, prompt: str) -> str:
        if scenario == "success":
            digest = hashlib.sha256(f"{self.seed}:{prompt}".encode()).hexdigest()[:16]
            return f"MOCK_OK[{digest}]: детерминированный ответ мок-провайдера"
        return _SCENARIO_RESPONSES[scenario]

    def _record(self, kind: str, scenario: Scenario, prompt: str, params: dict[str, Any]) -> None:
        self.calls.append({"kind": kind, "scenario": scenario, "prompt": prompt, "params": params})

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Возвращает детерминированный ответ по (seed, prompt) или сценарий валидатора."""
        scenario, clean_prompt = self._resolve(prompt)
        self._record("generate", scenario, clean_prompt, kwargs)
        return self._response_for(scenario, clean_prompt)

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Стримит ответ кусками по 16 символов (детерминизм как у generate)."""
        scenario, clean_prompt = self._resolve(prompt)
        self._record("stream", scenario, clean_prompt, kwargs)
        response = self._response_for(scenario, clean_prompt)
        for start in range(0, len(response), 16):
            yield response[start : start + 16]
            await asyncio.sleep(0)

    async def embeddings(self, texts: Sequence[str], **kwargs: Any) -> list[list[float]]:
        """Возвращает единичные векторы длины 1536, детерминированные по (seed, текст)."""
        self.calls.append({"kind": "embeddings", "count": len(texts), "params": kwargs})
        vectors: list[list[float]] = []
        for item in texts:
            rng = random.Random(f"{self.seed}:{item}")
            vector = [rng.uniform(-1.0, 1.0) for _ in range(_EMBEDDING_DIM)]
            norm = sum(value**2 for value in vector) ** 0.5
            vectors.append([value / norm for value in vector])
        return vectors


class _MockObservation:
    """Заглушка наблюдения LangFuse: контекст-менеджер, записывает вызовы в журнал."""

    def __init__(self, journal: list[dict[str, Any]], name: str, params: dict[str, Any]) -> None:
        self._journal = journal
        self._name = name
        journal.append({"name": name, "params": params})

    def __enter__(self) -> _MockObservation:
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        return False

    async def __aenter__(self) -> _MockObservation:
        return self

    async def __aexit__(self, *exc_info: object) -> Literal[False]:
        return False

    def end(self, **kwargs: Any) -> None:
        self._journal.append({"name": f"{self._name}.end", "params": kwargs})

    def update(self, **kwargs: Any) -> None:
        self._journal.append({"name": f"{self._name}.update", "params": kwargs})


class MockLangfuse:
    """Мок-клиент LangFuse v4 для офлайн-тестов (Принцип III).

    Не выполняет ни одного сетевого вызова: операции пишутся в общий журнал
    ``calls`` (экземпляр шарит журнал с другими мок-клиентами прогона —
    фикстура ``mock_langfuse`` очищает его перед каждым тестом).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.calls = _MOCK_LANGFUSE_CALLS

    def start_observation(self, *, name: str, **kwargs: Any) -> _MockObservation:
        return _MockObservation(self.calls, name, kwargs)

    def flush(self) -> None:
        self.calls.append({"name": "flush", "params": {}})

    def shutdown(self) -> None:
        self.calls.append({"name": "shutdown", "params": {}})

    def __getattr__(self, name: str) -> Any:
        """Любой непокрытый API (trace/span/event/…) — запись без сети."""
        if name.startswith("_") or "calls" not in self.__dict__:
            raise AttributeError(name)

        def _recorded(**kwargs: Any) -> _MockObservation:
            return _MockObservation(self.calls, name, kwargs)

        return _recorded


def _passthrough_observe(*args: Any, **kwargs: Any) -> Any:
    """Декоратор ``langfuse.observe`` без трассировки: функция возвращается как есть."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def decorator(func: Any) -> Any:
        return func

    return decorator


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Session-контейнер стенда БД: PostgreSQL 16 + PostGIS + pgvector.

    Образ локальной сборки (см. ``_ensure_db_image``); после старта — проверки
    ``PostGIS_Version()`` и состава ``pg_extension``.
    """
    _ensure_db_image()
    container = PostgresContainer(
        DB_IMAGE,
        username="glist",
        password="glist_test",
        dbname="glist_test",
        driver="asyncpg",
    )
    container.start()
    try:
        asyncio.run(_verify_postgis(container.get_connection_url()))
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """DSN asyncpg для тестовой БД; контейнер один на весь прогон pytest."""
    return postgres_container.get_connection_url(driver="asyncpg")


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Async-движок (asyncpg, NullPool): создаётся и закрывается в цикле одного теста."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия БД с изоляцией: изменения теста откатываются в teardown.

    Внешняя транзакция не коммитится, ``commit()`` теста закрывает только
    savepoint-уровень — после теста ``connection.rollback()`` убирает все данные,
    схема (когда появится в T013+) остаётся нетронутой.
    """
    async with db_engine.connect() as connection:
        await connection.begin()
        await connection.begin_nested()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        async with factory() as session:
            yield session
        await connection.rollback()


@pytest.fixture(scope="session")
def seed() -> int:
    """SEED из окружения (по умолчанию 42) — воспроизводимость прогонов (Принцип II)."""
    return int(os.environ.get("SEED", "42"))


@pytest.fixture
def mock_llm(seed: int) -> MockLLMProvider:
    """Детерминированный мок LLM-провайдера (Принцип VI; контракт T020/T023).

    Сценарий в тесте: ``mock_llm.scenario = "analysis_error"`` или директива
    ``[[scenario:…]]`` в промпте.
    """
    return MockLLMProvider(seed=seed)


@pytest.fixture(autouse=True)
def testing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Офлайн-окружение прогона: тесты не ходят в реальные внешние сервисы.

    ``GLIST_TESTING=1`` — флаг для кода стенда (T025 обязан его уважать: в тестах
    реальная трассировка отключается); ``LANGFUSE_*`` — фиктивные значения на
    мёртвый локальный адрес; ``OPENAI_API_KEY`` вычищается, чтобы код не мог уйти
    в реальный API (Принцип VI, Принцип III).
    """
    monkeypatch.setenv("GLIST_TESTING", "1")
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:1999")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield


@pytest.fixture
def mock_langfuse(monkeypatch: pytest.MonkeyPatch) -> MockLangfuse:
    """Мок-клиент LangFuse: офлайн-тесты трассировки (Принцип III, контракт T089).

    Подменяет ``langfuse.Langfuse``/``langfuse.observe`` — в том числе в уже
    импортированном ``glist.trace.langfuse`` (T025), если модуль загружен:
    сетевых вызовов нет, все операции падают в общий журнал ``calls``.
    """
    _MOCK_LANGFUSE_CALLS.clear()
    monkeypatch.setenv("GLIST_TESTING", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:1999")
    monkeypatch.setattr(langfuse, "Langfuse", MockLangfuse)
    monkeypatch.setattr(langfuse, "observe", _passthrough_observe, raising=False)
    trace_module = sys.modules.get("glist.trace.langfuse")
    if trace_module is not None:
        monkeypatch.setattr(trace_module, "Langfuse", MockLangfuse, raising=False)
        monkeypatch.setattr(trace_module, "observe", _passthrough_observe, raising=False)
    return MockLangfuse()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент для тестовых запросов поверх FastAPI (``ASGITransport``)."""
    try:
        # Импорт через importlib: mypy не требует статического существования
        # glist.main до T036, а при его появлении не остаётся неиспользуемого ignore.
        app_module = importlib.import_module("glist.main")
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".")[0] == "glist":
            raise RuntimeError(
                "Модуль glist.main ещё не создан (T036): фикстура client заработает "
                "после появления FastAPI-приложения"
            ) from exc
        raise
    app: Any = cast(Any, app_module).app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
