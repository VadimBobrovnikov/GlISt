"""Слой пространственных запросов (задача T019).

Все операции с геометрией выполняются исключительно средствами PostGIS
(Принцип IV Конституции). Никаких вычислений на стороне Python (Shapely и т.д.).

Публичный API:

- :func:`calculate_area` — площадь геометрии в кв. метрах.
- :func:`check_intersects` — проверка пересечения двух геометрий.
- :func:`make_buffer` — буфер заданного радиуса в метрах.
- :func:`validate_geometry` — проверка валидности и исправление через ``ST_MakeValid``.
- :func:`check_srid` — проверка, что SRID геометрии равен 4326.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def calculate_area(session: AsyncSession, geom: str) -> float:
    """Возвращает площадь геометрии в квадратных метрах.

    Геометрия приводится к типу ``geography`` (WGS84), поэтому
    :func:`ST_Area` возвращает площадь в квадратных метрах, а не в
    квадратных градусах (Принцип IV — только PostGIS).

    Args:
        session: Асинхронная сессия БД.
        geom: Геометрия в формате WKT (SRID 4326).

    Returns:
        Площадь в квадратных метрах.
    """
    result = await session.scalar(
        text("SELECT ST_Area(ST_GeomFromText(:geom, 4326)::geography)"),
        {"geom": geom},
    )
    return float(result)


async def check_intersects(session: AsyncSession, geom1: str, geom2: str) -> bool:
    """Проверяет, пересекаются ли две геометрии.

    Args:
        session: Асинхронная сессия БД.
        geom1: Первая геометрия в формате WKT.
        geom2: Вторая геометрия в формате WKT.

    Returns:
        ``True``, если геометрии пересекаются, иначе ``False``.
    """
    result = await session.scalar(
        text("SELECT ST_Intersects(ST_GeomFromText(:geom1, 4326), ST_GeomFromText(:geom2, 4326))"),
        {"geom1": geom1, "geom2": geom2},
    )
    return bool(result)


async def make_buffer(session: AsyncSession, geom: str, radius_m: float) -> str:
    """Создаёт буфер заданного радиуса вокруг геометрии.

    Буфер считается в метрах (через приведение к ``geography``),
    результат возвращается в формате WKT (Принцип IV).

    Args:
        session: Асинхронная сессия БД.
        geom: Геометрия в формате WKT (SRID 4326).
        radius_m: Радиус буфера в метрах.

    Returns:
        Геометрия буфера в формате WKT.
    """
    result = await session.scalar(
        text(
            "SELECT ST_AsText("
            "ST_Buffer(ST_GeomFromText(:geom, 4326)::geography, :radius_m)"
            "::geometry)"
        ),
        {"geom": geom, "radius_m": radius_m},
    )
    return str(result)


async def validate_geometry(session: AsyncSession, geom: str) -> tuple[bool, bool]:
    """Проверяет валидность геометрии и отмечает необходимость исправления.

    Использует :func:`ST_IsValid` для проверки и :func:`ST_MakeValid`
    для исправления при необходимости (Принцип IV).

    Args:
        session: Асинхронная сессия БД.
        geom: Геометрия в формате WKT.

    Returns:
        Кортеж ``(is_valid, was_fixed)``, где:
        - ``is_valid`` — ``True``, если геометрия валидна изначально;
        - ``was_fixed`` — ``True``, если геометрия была невалидна и
          требует исправления через ``ST_MakeValid``.
    """
    is_valid = await session.scalar(
        text("SELECT ST_IsValid(ST_GeomFromText(:geom))"),
        {"geom": geom},
    )
    is_valid_bool = bool(is_valid)
    return (is_valid_bool, not is_valid_bool)


async def check_srid(session: AsyncSession, geom: str) -> bool:
    """Проверяет, что SRID геометрии равен 4326 (WGS84).

    Args:
        session: Асинхронная сессия БД.
        geom: Геометрия в формате WKT (может содержать префикс SRID через EWKT).

    Returns:
        ``True``, если SRID равен 4326, иначе ``False``.
    """
    result = await session.scalar(
        text("SELECT ST_SRID(ST_GeomFromEWKT(:geom)) = 4326"),
        {"geom": geom},
    )
    return bool(result)
