"""Проверка результата: RGB-композиты до/после и NDVI в точке."""

from __future__ import annotations

import asyncio
import uuid
from io import BytesIO
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.db.models import AnalysisResult, AnalysisRun
from glist.tools.raster_ops import (
    calculate_ndvi,
    mask_scl,
    median_composite,
    read_cog_window,
)

router = APIRouter(prefix="/api/v1")

_REFLECTANCE_SCALE = 0.0001
_BAND_CONCURRENCY = 4
# Рабочий порог доли валидных пикселей в окне точки: ниже него переход
# не классифицируется. Порог не подтверждён измерением точности.
_MIN_SAMPLE_VALID_FRACTION = 0.2


def _as_dict(value: object) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _band_scaling(
    scene: dict[str, Any],
    prefix: str,
    default_scale: float,
) -> tuple[float, float]:
    """Единая коррекция band из сохранённых метаданных сцены."""
    raw_scale = scene.get(f"{prefix}Scale")
    raw_offset = scene.get(f"{prefix}Offset")
    if isinstance(raw_scale, (int, float)) and float(raw_scale) > 0:
        offset = float(raw_offset) if isinstance(raw_offset, (int, float)) else 0.0
        return float(raw_scale), offset
    return default_scale, 0.0


async def _load_diagnostics(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> tuple[AnalysisResult, dict[str, Any]] | None:
    run = await session.get(AnalysisRun, run_id)
    if run is None:
        return None
    result = (
        await session.execute(
            select(AnalysisResult).where(AnalysisResult.run_id == run_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if result is None and run.reused_result_id is not None:
        result = await session.get(AnalysisResult, run.reused_result_id)
    if result is None:
        return None
    diagnostics = _as_dict(_as_dict(result.sources_summary).get("diagnostics"))
    return result, diagnostics


def _role_scenes(diagnostics: dict[str, Any], role: str) -> list[dict[str, Any]]:
    scenes = _as_dict(diagnostics.get("scenes"))
    value = scenes.get(role)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _bbox(diagnostics: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = diagnostics.get("bbox")
    if isinstance(value, list) and len(value) == 4:
        try:
            return tuple(float(item) for item in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    return None


async def _read_band(
    url: str,
    bbox: tuple[float, float, float, float],
    scale: float,
    out_shape: tuple[int, int] | None,
    semaphore: asyncio.Semaphore,
    offset: float = 0.0,
) -> np.ndarray | None:
    async with semaphore:
        try:
            data, _ = await asyncio.to_thread(
                read_cog_window,
                url,
                bbox,
                1,
                scale,
                out_shape,
                offset=offset,
            )
        except Exception:  # noqa: BLE001 — одна сцена не роняет endpoint
            return None
    return data if data.size else None


async def _scene_rgb(
    scene: dict[str, Any],
    bbox: tuple[float, float, float, float],
    semaphore: asyncio.Semaphore,
) -> list[np.ndarray] | None:
    """RGB-каналы одной сцены с SCL-маской (None, если сцена не прочитана)."""
    red_url = scene.get("redUrl")
    green_url = scene.get("greenUrl")
    blue_url = scene.get("blueUrl")
    scl_url = scene.get("sclUrl")
    if not (red_url and green_url and blue_url and scl_url):
        return None
    red_scale, red_offset = _band_scaling(scene, "red", _REFLECTANCE_SCALE)
    green_scale, green_offset = _band_scaling(scene, "green", _REFLECTANCE_SCALE)
    blue_scale, blue_offset = _band_scaling(scene, "blue", _REFLECTANCE_SCALE)
    scl_scale, scl_offset = _band_scaling(scene, "scl", 1.0)
    red = await _read_band(str(red_url), bbox, red_scale, None, semaphore, red_offset)
    if red is None:
        return None
    shape = (int(red.shape[0]), int(red.shape[1]))
    green = await _read_band(
        str(green_url), bbox, green_scale, shape, semaphore, green_offset
    )
    blue = await _read_band(
        str(blue_url), bbox, blue_scale, shape, semaphore, blue_offset
    )
    scl = await _read_band(
        str(scl_url), bbox, scl_scale, shape, semaphore, scl_offset
    )
    if green is None or blue is None or scl is None:
        return None
    invalid = mask_scl(scl)
    rows = min(red.shape[0], green.shape[0], blue.shape[0])
    cols = min(red.shape[1], green.shape[1], blue.shape[1])
    channels: list[np.ndarray] = []
    for channel in (red, green, blue):
        values = channel[:rows, :cols].astype(np.float32)
        channels.append(np.where(invalid[:rows, :cols], np.nan, values))
    return channels


async def _rgb_composite(
    scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> np.ndarray | None:
    semaphore = asyncio.Semaphore(_BAND_CONCURRENCY)
    results = await asyncio.gather(
        *(_scene_rgb(scene, bbox, semaphore) for scene in scenes)
    )
    channels: list[list[np.ndarray]] = [[], [], []]
    for scene_channels in results:
        if scene_channels is None:
            continue
        for index, values in enumerate(scene_channels):
            channels[index].append(values)
    if not any(channels):
        return None
    rows = min(item.shape[0] for values in channels for item in values)
    cols = min(item.shape[1] for values in channels for item in values)
    result = np.zeros((rows, cols, 3), dtype=np.uint8)
    for index, channel_arrays in enumerate(channels):
        if not channel_arrays:
            continue
        composite = median_composite(
            [item[:rows, :cols] for item in channel_arrays]
        )
        valid = np.isfinite(composite)
        if not valid.any():
            continue
        low, high = np.nanpercentile(composite[valid], [2, 98])
        span = max(float(high - low), 1e-4)
        stretched = np.clip((composite - low) / span, 0, 1)
        stretched = np.where(valid, stretched, 0)
        result[:, :, index] = (stretched * 255).astype(np.uint8)
    return result


@router.get("/runs/{runId}/preview")
async def run_preview(  # noqa: B008
    runId: uuid.UUID,
    period: Literal["start", "end", "unknown"] = Query(default="end"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """PNG композита сцен расчёта или отдельного слоя unknown.

    ``period=unknown`` отдаёт RGBA-маску пикселей, невалидных хотя бы на
    одной дате (облака/тени/снег/NoData), — отдельный слой карты.
    """
    loaded = await _load_diagnostics(session, runId)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Результат не найден")
    _result, diagnostics = loaded
    bbox = _bbox(diagnostics)
    if bbox is None:
        raise HTTPException(
            status_code=409,
            detail="Для этого запуска нет сохранённой области анализа",
        )
    if period == "unknown":
        rgba = await _unknown_rgba(
            _role_scenes(diagnostics, "start"),
            _role_scenes(diagnostics, "end"),
            bbox,
        )
        if rgba is None:
            raise HTTPException(
                status_code=409,
                detail="Не удалось построить маску unknown",
            )
        buffer = BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    scenes = _role_scenes(diagnostics, period)
    if not scenes:
        raise HTTPException(
            status_code=409,
            detail="Для этого запуска нет сохранённых сцен/области анализа",
        )
    rgb = await _rgb_composite(scenes, bbox)
    if rgb is None:
        raise HTTPException(status_code=409, detail="Не удалось прочитать сцены")
    buffer = BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


async def _scene_ndvi(
    scene: dict[str, Any],
    bbox: tuple[float, float, float, float],
    semaphore: asyncio.Semaphore,
) -> np.ndarray | None:
    """NDVI одной сцены с SCL-маской (None, если не прочитана)."""
    red_url = scene.get("redUrl")
    nir_url = scene.get("nirUrl")
    scl_url = scene.get("sclUrl")
    if not (red_url and nir_url and scl_url):
        return None
    red_scale, red_offset = _band_scaling(scene, "red", _REFLECTANCE_SCALE)
    nir_scale, nir_offset = _band_scaling(scene, "nir", _REFLECTANCE_SCALE)
    scl_scale, scl_offset = _band_scaling(scene, "scl", 1.0)
    red = await _read_band(str(red_url), bbox, red_scale, None, semaphore, red_offset)
    if red is None:
        return None
    shape = (int(red.shape[0]), int(red.shape[1]))
    nir = await _read_band(
        str(nir_url), bbox, nir_scale, shape, semaphore, nir_offset
    )
    scl = await _read_band(
        str(scl_url), bbox, scl_scale, shape, semaphore, scl_offset
    )
    if nir is None or scl is None:
        return None
    invalid = mask_scl(scl)
    red_f = np.where(invalid, np.nan, red.astype(np.float32))
    nir_f = np.where(invalid, np.nan, nir.astype(np.float32))
    return calculate_ndvi(red_f, nir_f)


async def _period_ndvi_composite(
    scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> tuple[float | None, float]:
    """Медианный NDVI периода и доля валидных пикселей в окне точки.

    Композит строится по тем же сценам и SCL-маске, что и расчёт анализа,
    поэтому точка сверяется с фактическими данными, а не с отдельной выборкой.
    Сцены читаются параллельно: последовательное открытие COG не успевало
    в клиентский таймаут.
    """
    semaphore = asyncio.Semaphore(_BAND_CONCURRENCY)
    results = await asyncio.gather(
        *(_scene_ndvi(scene, bbox, semaphore) for scene in scenes)
    )
    arrays = [item for item in results if item is not None]
    if not arrays:
        return None, 0.0
    rows = min(int(item.shape[0]) for item in arrays)
    cols = min(int(item.shape[1]) for item in arrays)
    composite = median_composite([item[:rows, :cols] for item in arrays])
    valid = np.isfinite(composite)
    if not valid.any():
        return None, 0.0
    return float(np.nanmedian(composite[valid])), float(valid.mean())


async def _scene_valid(
    scene: dict[str, Any],
    bbox: tuple[float, float, float, float],
    semaphore: asyncio.Semaphore,
) -> np.ndarray | None:
    scl_url = scene.get("sclUrl")
    if not scl_url:
        return None
    scl_scale, scl_offset = _band_scaling(scene, "scl", 1.0)
    scl = await _read_band(
        str(scl_url), bbox, scl_scale, None, semaphore, scl_offset
    )
    if scl is None:
        return None
    return ~mask_scl(scl)


async def _period_valid_mask(
    scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> np.ndarray | None:
    """Валидные пиксели периода (SCL) — та же маска, что у композита."""
    semaphore = asyncio.Semaphore(_BAND_CONCURRENCY)
    results = await asyncio.gather(
        *(_scene_valid(scene, bbox, semaphore) for scene in scenes)
    )
    masks = [item for item in results if item is not None]
    if not masks:
        return None
    rows = min(int(item.shape[0]) for item in masks)
    cols = min(int(item.shape[1]) for item in masks)
    stacked = np.stack([item[:rows, :cols] for item in masks], axis=0)
    return stacked.any(axis=0)


async def _unknown_rgba(
    start_scenes: list[dict[str, Any]],
    end_scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> np.ndarray | None:
    """RGBA-маска unknown: невалидно хотя бы на одной дате (отдельный слой)."""
    start_valid = await _period_valid_mask(start_scenes, bbox)
    end_valid = await _period_valid_mask(end_scenes, bbox)
    if start_valid is None or end_valid is None:
        return None
    rows = min(int(start_valid.shape[0]), int(end_valid.shape[0]))
    cols = min(int(start_valid.shape[1]), int(end_valid.shape[1]))
    if rows <= 0 or cols <= 0:
        return None
    unknown = ~(start_valid[:rows, :cols] & end_valid[:rows, :cols])
    rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
    rgba[unknown] = (90, 90, 90, 160)
    return rgba


@router.get("/runs/{runId}/sample")
async def run_sample(  # noqa: B008
    runId: uuid.UUID,
    lat: float,
    lon: float,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Возвращает NDVI до/после и класс перехода для выбранной точки."""
    loaded = await _load_diagnostics(session, runId)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Результат не найден")
    _result, diagnostics = loaded
    classification = _as_dict(diagnostics.get("classification"))
    threshold = float(classification.get("ndviThreshold") or 0.25)
    half = 0.0015
    bbox = (lon - half, lat - half, lon + half, lat + half)
    start_ndvi, start_valid = await _period_ndvi_composite(
        _role_scenes(diagnostics, "start"), bbox
    )
    end_ndvi, end_valid = await _period_ndvi_composite(
        _role_scenes(diagnostics, "end"), bbox
    )
    data_valid = (
        start_ndvi is not None
        and end_ndvi is not None
        and start_valid >= _MIN_SAMPLE_VALID_FRACTION
        and end_valid >= _MIN_SAMPLE_VALID_FRACTION
    )
    if not data_valid:
        transition = "unknown"
    else:
        assert start_ndvi is not None and end_ndvi is not None
        start_veg = start_ndvi >= threshold
        end_veg = end_ndvi >= threshold
        if start_veg and end_veg:
            transition = "retained"
        elif start_veg and not end_veg:
            transition = "lost"
        elif not start_veg and end_veg:
            transition = "new"
        else:
            transition = "stable_non_vegetation"
    return {
        "lat": lat,
        "lon": lon,
        "ndviBefore": start_ndvi,
        "ndviAfter": end_ndvi,
        "ndviThreshold": threshold,
        "transition": transition,
        "dataValid": data_valid,
        "validFraction": {"start": start_valid, "end": end_valid},
        "minValidFraction": _MIN_SAMPLE_VALID_FRACTION,
        "sampleWindowDeg": half * 2,
        "actualDates": diagnostics.get("actualDates"),
        "requestedPeriod": diagnostics.get("requestedPeriod"),
        "periodSelection": diagnostics.get("periodSelection"),
    }