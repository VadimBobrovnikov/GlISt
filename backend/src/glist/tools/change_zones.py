"""Формирование зон изменений из растровых масок (задача T053).

Полигонализация — ``rasterio.features.shapes``; очистка и фильтр минимальной
площади — ``rasterio.features.sieve``; перепроецирование в EPSG:4326 —
``rasterio.warp.transform_geom``. Площади в Python не считаются: это делает
PostGIS при записи в ``change_zone`` (Принцип IV).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import rasterio.features
import rasterio.warp
from rasterio.transform import Affine

TARGET_CRS = "EPSG:4326"
DEFAULT_MIN_AREA_M2 = 1000.0
CHANGE_CLASSES = ("retained", "lost", "new")


def pixel_area_m2(transform: Affine) -> float:
    """Возвращает площадь пикселя растра в квадратных метрах."""
    determinant = abs(
        float(transform.a) * float(transform.e)
        - float(transform.b) * float(transform.d)
    )
    return determinant


def mask_to_zones(
    mask: np.ndarray,
    transform: Affine,
    crs: Any,
    change_class: str,
    territory_name: str,
    min_area_m2: float = DEFAULT_MIN_AREA_M2,
) -> list[dict[str, Any]]:
    """Полигонализует бинарную маску класса изменений.

    Args:
        mask: Бинарная маска (True = пиксель принадлежит классу).
        transform: Affine-трансформ окна.
        crs: CRS исходного растра (например, ``EPSG:32637``).
        change_class: ``retained`` / ``lost`` / ``new``.
        territory_name: Имя территории для атрибутов.
        min_area_m2: Минимальная площадь зоны (по умолчанию 1000 м²).

    Returns:
        Список зон с GeoJSON-геометрией в EPSG:4326. Площадь не считается.
    """
    if change_class not in CHANGE_CLASSES:
        raise ValueError(f"Недопустимый change_class: {change_class!r}")

    binary = mask.astype(bool).astype(np.uint8)
    if not binary.any():
        return []

    area_per_pixel = pixel_area_m2(transform)
    if area_per_pixel <= 0:
        return []
    sieve_size = max(1, int(math.ceil(min_area_m2 / area_per_pixel)))
    cleaned = rasterio.features.sieve(binary, size=sieve_size, connectivity=8)

    zones: list[dict[str, Any]] = []
    for geometry, value in rasterio.features.shapes(
        cleaned,
        mask=cleaned.astype(bool),
        transform=transform,
        connectivity=8,
    ):
        if int(value) != 1:
            continue
        geometry_wgs84 = rasterio.warp.transform_geom(
            crs,
            TARGET_CRS,
            geometry,
            precision=6,
        )
        zones.append(
            {
                "territory_name": territory_name,
                "change_class": change_class,
                "geometry": geometry_wgs84,
                "cleaned": True,
            }
        )
    return zones


def build_change_zones(
    masks: dict[str, np.ndarray],
    transform: Affine,
    crs: Any,
    territory_name: str,
    min_area_m2: float = DEFAULT_MIN_AREA_M2,
) -> list[dict[str, Any]]:
    """Полигонализует маски всех трёх классов изменений."""
    zones: list[dict[str, Any]] = []
    for change_class in CHANGE_CLASSES:
        mask = masks.get(change_class)
        if mask is None:
            continue
        zones.extend(
            mask_to_zones(
                mask,
                transform,
                crs,
                change_class,
                territory_name,
                min_area_m2,
            )
        )
    return zones