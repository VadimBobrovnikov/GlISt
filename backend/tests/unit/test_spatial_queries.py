"""Unit-тесты слоя пространственных запросов (задача T019).

Все функции проверяются на реальном PostGIS через фикстуру ``db_session``.
Все геометрические операции выполняются средствами PostGIS (Принцип IV).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.spatial_queries import (
    calculate_area,
    check_intersects,
    check_srid,
    make_buffer,
    validate_geometry,
)

# Тестовые геометрии в формате WKT (SRID 4326 = WGS84).
_POINT_MOSCOW = "POINT(37.6173 55.7558)"
_POINT_SPb = "POINT(30.3141 59.9391)"
_POLYGON_SMALL = "POLYGON((37.6 55.7, 37.7 55.7, 37.7 55.8, 37.6 55.8, 37.6 55.7))"
_POLYGON_LARGE = "POLYGON((37.0 55.0, 38.0 55.0, 38.0 56.0, 37.0 56.0, 37.0 55.0))"
_INVALID_POLYGON = "POLYGON((0 0, 1 1, 0 1, 1 0, 0 0))"  # самопересекающийся (бабочка)


@pytest.mark.asyncio
async def test_calculate_area_returns_square_meters(
    db_session: AsyncSession,
) -> None:
    """Площадь полигона возвращается в квадратных метрах."""
    area = await calculate_area(db_session, _POLYGON_LARGE)
    # Один квадратный градус на широте Москвы ≈ 12300 м × 111000 м ≈ 1.37e9 кв.м.
    # Полигон 1×1 градус должен давать площадь порядка миллиарда кв.м.
    assert area > 1_000_000_000


@pytest.mark.asyncio
async def test_calculate_area_point_is_zero(db_session: AsyncSession) -> None:
    """Площадь точки равна нулю."""
    area = await calculate_area(db_session, _POINT_MOSCOW)
    assert area == 0.0


@pytest.mark.asyncio
async def test_check_intersects_true(db_session: AsyncSession) -> None:
    """Пересекающиеся поликоны возвращают True."""
    poly1 = "POLYGON((37.6 55.7, 37.8 55.7, 37.8 55.9, 37.6 55.9, 37.6 55.7))"
    poly2 = "POLYGON((37.7 55.8, 37.9 55.8, 37.9 56.0, 37.7 56.0, 37.7 55.8))"
    assert await check_intersects(db_session, poly1, poly2) is True


@pytest.mark.asyncio
async def test_check_intersects_false(db_session: AsyncSession) -> None:
    """Непересекающиеся геометрии возвращают False."""
    poly1 = "POLYGON((37.0 55.0, 37.1 55.0, 37.1 55.1, 37.0 55.1, 37.0 55.0))"
    poly2 = "POLYGON((37.9 55.9, 38.0 55.9, 38.0 56.0, 37.9 56.0, 37.9 55.9))"
    assert await check_intersects(db_session, poly1, poly2) is False


@pytest.mark.asyncio
async def test_check_intersects_point_and_polygon(db_session: AsyncSession) -> None:
    """Точка внутри полигона — пересечение."""
    poly = "POLYGON((37.6 55.7, 37.8 55.7, 37.8 55.9, 37.6 55.9, 37.6 55.7))"
    point_inside = "POINT(37.7 55.8)"
    point_outside = "POINT(38.5 56.5)"
    assert await check_intersects(db_session, poly, point_inside) is True
    assert await check_intersects(db_session, poly, point_outside) is False


@pytest.mark.asyncio
async def test_make_buffer_returns_wkt(db_session: AsyncSession) -> None:
    """Буфер возвращает WKT-геометрию."""
    result = await make_buffer(db_session, _POINT_MOSCOW, 1000.0)
    assert result.startswith("POLYGON((") or result.startswith("POLYGON((")


@pytest.mark.asyncio
async def test_make_buffer_radius_zero(db_session: AsyncSession) -> None:
    """Буфер с радиусом 0 — вырожденный полигон вокруг точки."""
    result = await make_buffer(db_session, _POINT_MOSCOW, 0.0)
    assert "POLYGON" in result


@pytest.mark.asyncio
async def test_validate_geometry_valid(db_session: AsyncSession) -> None:
    """Валидная геометрия: is_valid=True, was_fixed=False."""
    is_valid, was_fixed = await validate_geometry(db_session, _POLYGON_SMALL)
    assert is_valid is True
    assert was_fixed is False


@pytest.mark.asyncio
async def test_validate_geometry_invalid(db_session: AsyncSession) -> None:
    """Невалидная геометрия: is_valid=False, was_fixed=True."""
    is_valid, was_fixed = await validate_geometry(db_session, _INVALID_POLYGON)
    assert is_valid is False
    assert was_fixed is True


@pytest.mark.asyncio
async def test_check_srid_4326(db_session: AsyncSession) -> None:
    """Геометрия с SRID=4326 проходит проверку."""
    assert await check_srid(db_session, "SRID=4326;" + _POINT_MOSCOW) is True


@pytest.mark.asyncio
async def test_check_srid_other(db_session: AsyncSession) -> None:
    """Геометрия с другим SRID не проходит проверку."""
    assert await check_srid(db_session, "SRID=3857;" + _POINT_MOSCOW) is False


@pytest.mark.asyncio
async def test_check_srid_no_srid(db_session: AsyncSession) -> None:
    """Геометрия без SRID не проходит проверку (SRID = 0)."""
    assert await check_srid(db_session, _POINT_MOSCOW) is False
