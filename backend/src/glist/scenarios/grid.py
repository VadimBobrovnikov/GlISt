"""Общая сетка анализа для сценариев: сборка тайлов и перепроецирование.

Сцены Sentinel-2 приходят из разных MGRS-тайлов (разные CRS/origin). Чтобы
сравнивать даты пиксель-в-пиксель, все наблюдения приводятся к одной сетке,
построенной по bbox территории в EPSG:3857 с заданным шагом.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds

TARGET_CRS = "EPSG:3857"


def target_grid(bbox: list[float], resolution_m: float = 30.0) -> dict[str, Any]:
    """Строит целевую сетку по bbox (EPSG:4326) в метрической проекции."""
    west, south, east, north = bbox
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", TARGET_CRS, west, south, east, north, densify_pts=21
    )
    width = max(1, int(round((right - left) / resolution_m)))
    height = max(1, int(round((top - bottom) / resolution_m)))
    transform = Affine(resolution_m, 0.0, left, 0.0, -resolution_m, top)
    return {
        "crs": TARGET_CRS,
        "transform": [float(value) for value in tuple(transform)[:6]],
        "shape": [height, width],
        "resolution": [resolution_m, resolution_m],
        "origin": [left, top],
        "pixelAreaM2": pixel_area_m2(bbox, resolution_m),
    }


def pixel_area_m2(bbox: list[float], resolution_m: float) -> float:
    """Площадь пикселя на земле: в EPSG:3857 масштаб зависит от широты."""
    mean_lat = (float(bbox[1]) + float(bbox[3])) / 2.0
    factor = math.cos(math.radians(mean_lat))
    return float(resolution_m) * float(resolution_m) * factor * factor


def affine_of(grid: dict[str, Any]) -> Affine:
    values = grid.get("transform") or [1, 0, 0, 0, -1, 0]
    return Affine(*[float(value) for value in values[:6]])


def warp_to_grid(
    data: np.ndarray,
    source_grid: dict[str, Any],
    target: dict[str, Any],
    *,
    resampling: Resampling = Resampling.bilinear,
) -> np.ndarray:
    """Перепроецирует массив на целевую сетку; вне данных — NaN."""
    destination = np.full(tuple(int(v) for v in target["shape"]), np.nan, dtype="float32")
    reproject(
        source=data.astype("float32"),
        destination=destination,
        src_transform=affine_of(source_grid),
        src_crs=source_grid["crs"],
        src_nodata=np.nan,
        dst_transform=affine_of(target),
        dst_crs=target["crs"],
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return destination


def warp_mask_to_grid(
    mask: np.ndarray,
    source_grid: dict[str, Any],
    target: dict[str, Any],
) -> np.ndarray:
    """Перепроецирует булеву маску (ближайший сосед), NaN → False."""
    warped = warp_to_grid(
        mask.astype("float32"),
        source_grid,
        target,
        resampling=Resampling.nearest,
    )
    return np.isfinite(warped) & (warped > 0.5)
