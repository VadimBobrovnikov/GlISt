"""Зависимости FastAPI (задача T036).

Предоставляет:
- :func:`get_current_user` — заглушка аутентификации (тестовый пользователь).
- :func:`get_session` — асинхронная сессия БД для эндпоинтов API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.session import get_session as _get_session


async def get_current_user() -> dict[str, Any]:
    """Возвращает тестового пользователя (заглушка аутентификации).

    В продакшене здесь должна быть проверка JWT/сессии из заголовков.
    Пока возвращает фиксированного пользователя для совместимости с роутерами.

    Returns:
        Словарь с полями ``id`` и ``display_name`` тестового пользователя.
    """
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "display_name": "Тестовый пользователь",
    }


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: выдаёт ``AsyncSession`` на время запроса.

    Используется в зависимостях роутеров:
    ``session: AsyncSession = Depends(get_session)``.

    Yields:
        Открытая ``AsyncSession``.
    """
    async for session in _get_session():
        yield session