"""Роутер проверки здоровья API (задача T037).

Endpoints:
- ``GET /api/v1/health`` — возвращает ``ok`` если БД и Redis доступны,
  иначе ``degraded``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from glist.api.deps import get_current_user, get_session
from glist.trace.events import get_publisher

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(  # noqa: B008
    session: Any = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    """Проверяет доступность БД и Redis.

    Args:
        session: Асинхронная сессия БД (внедрена через зависимость).
        current_user: Текущий пользователь (заглушка).

    Returns:
        JSON со статусом ``ok`` или ``degraded`` и кодом HTTP 200.
    """
    db_ok = await _check_db(session)
    redis_ok = await _check_redis()

    status = "ok" if db_ok and redis_ok else "degraded"
    return {"status": status}


async def _check_db(session: Any) -> bool:
    """Проверяет, что соединение с БД живо."""
    try:
        await session.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 — любая ошибка БД = degraded
        return False


async def _check_redis() -> bool:
    """Проверяет, что Redis отвечает на PING."""
    try:
        publisher = get_publisher()
        redis = await publisher._get_redis()
        result = await redis.ping()
        return bool(result)
    except Exception:  # noqa: BLE001 — любая ошибка Redis = degraded
        return False