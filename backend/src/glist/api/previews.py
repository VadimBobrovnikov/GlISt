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
from glist.tools.raster_ops import mask_scl, median_composite, read_cog_window

router = APIRouter(prefix="/api/v1")

_REFLECTANCE_SCALE = 0.0001
_BAND_CONCURRENCY = 4


def _as_dict(value: object) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


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
            )
        except Exception:  # noqa: BLE001 — одна сцена не роняет endpoint
            return None
    return data if data.size else None


async def _rgb_composite(
    scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> np.ndarray | None:
    semaphore = asyncio.Semaphore(_BAND_CONCURRENCY)
    channels: list[list[np.ndarray]] = [[], [], []]
    for scene in scenes:
        red_url = scene.get("redUrl")
        green_url = scene.get("greenUrl")
        blue_url = scene.get("blueUrl")
        scl_url = scene.get("sclUrl")
        if not (red_url and green_url and blue_url and scl_url):
            continue
        red = await _read_band(str(red_url), bbox, _REFLECTANCE_SCALE, None, semaphore)
        if red is None:
            continue
        shape = (int(red.shape[0]), int(red.shape[1]))
        green = await _read_band(
            str(green_url), bbox, _REFLECTANCE_SCALE, shape, semaphore
        )
        blue = await _read_band(
            str(blue_url), bbox, _REFLECTANCE_SCALE, shape, semaphore
        )
        scl = await _read_band(str(scl_url), bbox, 1.0, shape, semaphore)
        if green is None or blue is None or scl is None:
            continue
        invalid = mask_scl(scl)
        rows = min(red.shape[0], green.shape[0], blue.shape[0])
        cols = min(red.shape[1], green.shape[1], blue.shape[1])
        for index, channel in enumerate((red, green, blue)):
            values = channel[:rows, :cols].astype(np.float32)
            values = np.where(invalid[:rows, :cols], np.nan, values)
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
    period: Literal["start", "end"] = Query(default="end"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """Отдаёт PNG RGB-композита сцены/сцен, участвовавших в расчёте."""
    loaded = await _load_diagnostics(session, runId)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Результат не найден")
    _result, diagnostics = loaded
    bbox = _bbox(diagnostics)
    scenes = _role_scenes(diagnostics, period)
    if bbox is None or not scenes:
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


async def _scalar_ndvi(
    scenes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> float | None:
    semaphore = asyncio.Semaphore(_BAND_CONCURRENCY)
    values: list[float] = []
    for scene in scenes:
        red_url = scene.get("redUrl")
        nir_url = scene.get("nirUrl")
        scl_url = scene.get("sclUrl")
        if not (red_url and nir_url and scl_url):
            continue
        red = await _read_band(str(red_url), bbox, _REFLECTANCE_SCALE, None, semaphore)
        if red is None:
            continue
        shape = (int(red.shape[0]), int(red.shape[1]))
        nir = await _read_band(
            str(nir_url), bbox, _REFLECTANCE_SCALE, shape, semaphore
        )
        scl = await _read_band(str(scl_url), bbox, 1.0, shape, semaphore)
        if nir is None or scl is None:
            continue
        invalid = mask_scl(scl)
        red_f = np.where(invalid, np.nan, red.astype(np.float32))
        nir_f = np.where(invalid, np.nan, nir.astype(np.float32))
        denominator = nir_f + red_f
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = (nir_f - red_f) / denominator
        valid = np.isfinite(ndvi)
        if valid.any():
            values.append(float(np.nanmedian(ndvi[valid])))
    return float(np.median(values)) if values else None


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
    start_ndvi = await _scalar_ndvi(_role_scenes(diagnostics, "start"), bbox)
    end_ndvi = await _scalar_ndvi(_role_scenes(diagnostics, "end"), bbox)
    if start_ndvi is None or end_ndvi is None:
        transition = "unknown"
    else:
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
        "actualDates": diagnostics.get("actualDates"),
    }