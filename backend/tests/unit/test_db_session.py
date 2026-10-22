"""Unit-тесты доступа к БД (задача T012).

Проверяют контракт ``glist.db.session``: нормализацию DSN, выбор пула соединений
(``NullPool`` в тестовом режиме), общие engine/фабрику процесса, поведение
зависимости FastAPI (откат при ошибке, отсутствие неявного коммита) и
контекстного менеджера ARQ (коммит успеха, откат ошибки).

Интеграционные сценарии используют PostGIS-контейнер из ``conftest.py``
(Принцип VIII).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from glist.config import Settings
from glist.db import session as session_module
from glist.db.session import (
    create_engine,
    get_engine,
    get_session,
    get_session_ctx,
    get_sessionmaker,
    normalize_dsn,
    reset_engine,
)


@pytest.fixture
def settings_factory(required_env: dict[str, str]) -> Callable[..., Settings]:
    """Фабрика настроек из обязательного окружения (общая фикстура ``required_env``)."""
    return lambda **overrides: Settings(_env_file=None, **overrides)


@pytest.fixture
async def bound_sessionmaker(
    database_url: str, required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Фабрика сессий, привязанная к тестовой БД (контейнер ``database_url``)."""
    settings = Settings(_env_file=None, DATABASE_URL=database_url)
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)
    await reset_engine()
    try:
        yield get_sessionmaker()
    finally:
        await reset_engine()


@pytest.fixture
async def probe_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Отдельное соединение к тестовой БД — «наблюдатель» за пределами сессии."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def marker_table(probe_engine: AsyncEngine) -> AsyncIterator[str]:
    """Временная таблица для проверки фиксации/отката изменений."""
    async with probe_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE t012_marker (id integer PRIMARY KEY)"))
    try:
        yield "t012_marker"
    finally:
        async with probe_engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS t012_marker"))


async def _count(probe_engine: AsyncEngine) -> int:
    """Число строк в таблице ``t012_marker`` с независимого соединения."""
    async with probe_engine.connect() as connection:
        result = await connection.execute(text("SELECT count(*) FROM t012_marker"))
        return int(result.scalar_one())


def test_normalize_dsn_converts_sync_postgres() -> None:
    assert (
        normalize_dsn("postgresql://u:p@host:5432/db")
        == "postgresql+asyncpg://u:p@host:5432/db"
    )
    assert (
        normalize_dsn("postgres://u:p@host:5432/db")
        == "postgresql+asyncpg://u:p@host:5432/db"
    )


def test_normalize_dsn_keeps_async_and_foreign_schemes() -> None:
    async_dsn = "postgresql+asyncpg://u:p@host:5432/db"
    assert normalize_dsn(async_dsn) == async_dsn
    assert normalize_dsn("sqlite+aiosqlite:///tmp/x.db") == "sqlite+aiosqlite:///tmp/x.db"


async def test_create_engine_uses_null_pool_in_testing(
    settings_factory: object,
) -> None:
    settings = settings_factory()
    engine = create_engine(settings)
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        await engine.dispose()


async def test_create_engine_uses_standard_pool_outside_testing(
    settings_factory: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    settings = settings_factory()
    assert settings.glist_testing is False
    engine = create_engine(settings)
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 5
    finally:
        await engine.dispose()


async def test_engine_and_sessionmaker_are_process_wide(
    required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in required_env.items():
        monkeypatch.setenv(key, value)
    await reset_engine()
    try:
        assert get_engine() is get_engine()
        assert get_sessionmaker() is get_sessionmaker()
    finally:
        await reset_engine()


async def test_sessionmaker_keeps_attributes_after_commit(
    bound_sessionmaker: async_sessionmaker[AsyncSession], marker_table: str
) -> None:
    assert bound_sessionmaker.kw["expire_on_commit"] is False
    async with bound_sessionmaker() as session:
        await session.execute(text("INSERT INTO t012_marker VALUES (1)"))
        await session.commit()
        # Атрибуты не «протухают» после коммита (иначе DetachedInstanceError в API).
        assert session.bind is not None
        assert await session.scalar(text("SELECT count(*) FROM t012_marker")) == 1


async def test_get_session_does_not_commit_by_itself(
    bound_sessionmaker: async_sessionmaker[AsyncSession],
    probe_engine: AsyncEngine,
    marker_table: str,
) -> None:
    generator = get_session()
    session = await generator.__anext__()
    try:
        await session.execute(text("INSERT INTO t012_marker VALUES (1)"))
        assert await _count(probe_engine) == 0  # без commit изменения не видны
    finally:
        await generator.aclose()


async def test_get_session_rolls_back_on_error(
    bound_sessionmaker: async_sessionmaker[AsyncSession],
    probe_engine: AsyncEngine,
    marker_table: str,
) -> None:
    generator = get_session()
    session = await generator.__anext__()
    await session.execute(text("INSERT INTO t012_marker VALUES (1)"))
    with pytest.raises(RuntimeError, match="сбой обработки"):
        await generator.athrow(RuntimeError("сбой обработки"))
    await generator.aclose()
    assert await _count(probe_engine) == 0  # изменения откачены


async def test_get_session_ctx_commits_on_success(
    bound_sessionmaker: async_sessionmaker[AsyncSession],
    probe_engine: AsyncEngine,
    marker_table: str,
) -> None:
    async with get_session_ctx() as session:
        await session.execute(text("INSERT INTO t012_marker VALUES (1)"))
    assert await _count(probe_engine) == 1  # успешная работа зафиксирована


async def test_get_session_ctx_rolls_back_on_error(
    bound_sessionmaker: async_sessionmaker[AsyncSession],
    probe_engine: AsyncEngine,
    marker_table: str,
) -> None:
    with pytest.raises(RuntimeError, match="сбой задачи"):
        async with get_session_ctx() as session:
            await session.execute(text("INSERT INTO t012_marker VALUES (1)"))
            raise RuntimeError("сбой задачи")
    assert await _count(probe_engine) == 0  # неуспешная работа откачена
