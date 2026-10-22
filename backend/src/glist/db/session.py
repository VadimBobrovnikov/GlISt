"""Доступ к БД: async engine, фабрика сессий и зависимости (задача T012).

Модуль даёт три точки входа, одинаковые для API и для фоновых задач ARQ:

1. :func:`get_engine` — лениво созданный process-wide ``AsyncEngine`` (asyncpg).
2. :func:`get_sessionmaker` — ``async_sessionmaker`` с ``expire_on_commit=False``.
3. :func:`get_session` (зависимость FastAPI) и :func:`get_session_ctx`
   (контекстный менеджер ARQ) — сессия на единицу работы.

Правила (Принципы IX, X):

- DSN берётся только из настроек окружения; в коде нет значений по умолчанию
  и секретов.
- Пул соединений в тестовом режиме (``GLIST_TESTING=1``) — ``NullPool``: соединения
  не переиспользуются между тестами, состояние БД не протекает.
- Явных ``commit`` в слое доступа нет: коммит — осознанное действие вызывающего
  кода. ``get_session`` только откатывает несохранённое состояние при ошибке,
  ``get_session_ctx`` коммитит успешную работу и откатывает неуспешную.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from glist.config import Settings, get_settings

# Параметры стандартного пула: небольшой пул с переиспользованием соединений
# (воркеры ARQ и uvicorn — долгоживущие процессы, соединения не «протухают»).
_POOL_SIZE = 5
_MAX_OVERFLOW = 10
_POOL_RECYCLE_SECONDS = 1800

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def normalize_dsn(url: str) -> str:
    """Приводит DSN к асинхронному драйверу asyncpg.

    Args:
        url: DSN из переменной окружения ``DATABASE_URL``.

    Returns:
        DSN вида ``postgresql+asyncpg://…``. Схемы ``postgresql://`` и
        ``postgres://`` приводятся к асинхронному драйверу; уже корректные и
        не-PostgreSQL схемы возвращаются без изменений.
    """
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    return url


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Создаёт новый ``AsyncEngine`` для указанных настроек.

    В тестовом режиме (``GLIST_TESTING=1``) используется ``NullPool`` — каждое
    соединение закрывается сразу, поэтому состояние не протекает между
    тестами (Принцип VIII). В обычном режиме — стандартный пул с проверкой
    живости соединения.

    Args:
        settings: Настройки приложения; по умолчанию берутся из окружения
            (``get_settings()``).

    Returns:
        Готовый к использованию ``AsyncEngine`` (создание соединения ленивое).
    """
    resolved = settings if settings is not None else get_settings()
    url = normalize_dsn(resolved.database_url.get_secret_value())
    options: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if resolved.glist_testing:
        options["poolclass"] = NullPool
    else:
        options["pool_size"] = _POOL_SIZE
        options["max_overflow"] = _MAX_OVERFLOW
        options["pool_recycle"] = _POOL_RECYCLE_SECONDS
    return create_async_engine(url, **options)


def get_engine() -> AsyncEngine:
    """Возвращает общий ``AsyncEngine`` процесса, создавая его при первом вызове.

    Engine переиспользует пул соединений и живёт весь процесс: пересоздавать его
    на каждый запрос нельзя. Для тестов есть :func:`reset_engine`.

    Returns:
        ``AsyncEngine``, общий для всего процесса.
    """
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Возвращает общий ``async_sessionmaker`` процесса.

    ``expire_on_commit=False`` — после ``commit`` атрибуты объектов остаются
    доступными (иначе FastAPI-обработчик получил бы ``DetachedInstanceError``
    при сериализации ответа).

    Returns:
        Фабрика асинхронных сессий, общая для всего процесса.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: выдаёт сессию БД на время обработки запроса.

    Коммит не выполняется: транзакция остаётся открытой, а при исключении
    несохранённые изменения откатываются. Коммит делает вызывающий код — так
    границы транзакции видны и не теряются.

    Yields:
        Открытая ``AsyncSession``.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@contextlib.asynccontextmanager
async def get_session_ctx() -> AsyncIterator[AsyncSession]:
    """Контекстный менеджер для фоновых задач ARQ: сессия с фиксацией результата.

    При выходе из блока успешная работа коммитится, при исключении —
    откатывается. Использование: ``async with get_session_ctx() as session: …``.

    Yields:
        Открытая ``AsyncSession``; транзакция закрывается на выходе из блока.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def reset_engine() -> None:
    """Закрывает общий engine и сбрасывает фабрику сессий.

    Нужен в тестах (смена DSN, освобождение соединений контейнера) и при
    корректном завершении процесса.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
