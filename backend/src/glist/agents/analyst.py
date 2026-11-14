"""Analyst agent node (T059).

Uses stac_search for finding satellite imagery, raster_ops for NDVI calculation,
and landcover for detecting change zones.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Any

import numpy as np

from glist.tools.change_zones import build_change_zones
from glist.tools.geocode import geocode_territory
from glist.tools.landcover import classify_vegetation, detect_changes
from glist.tools.raster_ops import (
    calculate_ndvi,
    mask_scl,
    median_composite,
    read_cog_window,
)
from glist.tools.stac_search import search_sentinel2_scenes

logger = logging.getLogger("glist.agents.analyst")

DEFAULT_NDVI_THRESHOLD = 0.25
DEFAULT_MIN_AREA_M2 = 1000.0
MAX_CLOUD_COVER = 20
MAX_SCENES_PER_PERIOD = 3
COMPOSITE_WINDOW_DAYS = 30
EXPANDED_WINDOW_DAYS = 60
REFLECTANCE_SCALE = 0.0001
SCL_SCALE = 1.0
MAX_BBOX_SPAN_DEG = 0.12
MIN_VALID_COVERAGE = 0.1
_BAND_READ_CONCURRENCY = 4
_COMPOSITE_TIMEOUT_SECONDS = 180.0
_CHANGE_CLASSES = ("retained", "lost", "new")
_SEASON_BY_MONTH = {
    12: "зима",
    1: "зима",
    2: "зима",
    3: "весна",
    4: "весна",
    5: "весна",
    6: "лето",
    7: "лето",
    8: "лето",
    9: "осень",
    10: "осень",
    11: "осень",
}
# Резервные bbox для известных городов на случай недоступности Nominatim.
_STATIC_BBOXES: dict[str, list[float]] = {
    "москва": [37.35, 55.55, 37.95, 55.95],
}
_TESTING_VALUES = frozenset({"1", "true", "yes", "on"})


def _is_testing() -> bool:
    """True в offline-тестах (GLIST_TESTING): без сети и STAC."""
    return os.environ.get("GLIST_TESTING", "").strip().lower() in _TESTING_VALUES


def _parse_date(value: object) -> date | None:
    """Разбирает ISO-дату из состояния графа."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _window(center: date, days: int) -> tuple[str, str]:
    """Окно композитинга вокруг даты: ±days."""
    return (
        (center - timedelta(days=days)).isoformat(),
        (center + timedelta(days=days)).isoformat(),
    )


def _season_for(value: object) -> str | None:
    """Сезон даты сцены (для проверки сопоставимости)."""
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return _SEASON_BY_MONTH.get(parsed.month)


def _actual_dates(items: list[dict[str, Any]]) -> list[str]:
    """Фактические даты использованных сцен (YYYY-MM-DD)."""
    dates: list[str] = []
    for item in items:
        value = item.get("datetime")
        if isinstance(value, str) and value[:10] not in dates:
            dates.append(value[:10])
    return sorted(dates)


def _limit_bbox(bbox: list[float]) -> list[float]:
    """Ограничивает bbox разумным окном анализа (память и время)."""
    west, south, east, north = bbox
    if (
        east - west <= MAX_BBOX_SPAN_DEG
        and north - south <= MAX_BBOX_SPAN_DEG
    ):
        return [west, south, east, north]
    center_lon = (west + east) / 2
    center_lat = (south + north) / 2
    half = MAX_BBOX_SPAN_DEG / 2
    return [
        center_lon - half,
        center_lat - half,
        center_lon + half,
        center_lat + half,
    ]


def _bbox_from_payload(payload: object) -> list[float] | None:
    """Извлекает валидный bbox из результата геокодирования."""
    if not isinstance(payload, dict):
        return None
    bbox = payload.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = [float(item) for item in bbox]
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


async def _resolve_bbox(
    territory: dict[str, Any],
    name: str,
) -> list[float] | None:
    """Определяет bbox территории: из состояния или через Nominatim."""
    bbox = territory.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            values = [float(item) for item in bbox]
        except (TypeError, ValueError):
            values = []
        if len(values) == 4 and values[2] > values[0] and values[3] > values[1]:
            return _limit_bbox(values)
    geocoded = await geocode_territory(name)
    payload_bbox = _bbox_from_payload(geocoded)
    if payload_bbox is not None:
        return _limit_bbox(payload_bbox)
    for key, static_bbox in _STATIC_BBOXES.items():
        if key in name.lower():
            logger.warning(
                "Analyst: Nominatim недоступен, использую статический bbox для %s",
                name,
            )
            return _limit_bbox(static_bbox)
    return None


async def _search_period(
    bbox: list[float],
    center: date,
    role: str,
) -> list[dict[str, Any]]:
    """Ищет сцены в окне ±30 дней, при пустом результате — ±60 дней."""
    for days in (COMPOSITE_WINDOW_DAYS, EXPANDED_WINDOW_DAYS):
        start, end = _window(center, days)
        scenes = await search_sentinel2_scenes(
            bbox,
            start,
            end,
            max_cloud_cover=MAX_CLOUD_COVER,
            limit=10,
        )
        if not scenes:
            continue
        scenes.sort(key=lambda scene: float(scene.get("cloud_cover") or 100))
        selected = scenes[:MAX_SCENES_PER_PERIOD]
        for scene in selected:
            scene["role"] = role
        logger.info(
            "Analyst: %s — %d сцен в окне ±%d дней",
            role,
            len(selected),
            days,
        )
        return selected
    return []


def _scene_meta(scene: dict[str, Any]) -> dict[str, Any]:
    """Метаданные сцены для datasets/accepted и диагностики."""
    return {
        "source": "Sentinel-2",
        "collection": "sentinel-2-l2a",
        "sceneId": str(scene.get("id") or ""),
        "datetime": scene.get("datetime"),
        "cloudCover": scene.get("cloud_cover"),
        "role": scene.get("role"),
        "projEpsg": scene.get("proj_epsg"),
        "redUrl": scene.get("red_url"),
        "greenUrl": scene.get("green_url"),
        "blueUrl": scene.get("blue_url"),
        "nirUrl": scene.get("nir_url"),
        "sclUrl": scene.get("scl_url"),
    }


async def analyst_node(state: dict[str, Any]) -> dict[str, Any]:
    """Analyst node: реальный NDVI-анализ Sentinel-2 и зоны изменений."""
    if _is_testing():
        return {
            "next_agent": "visualizer",
            "messages": ["Analyst: тестовый режим (сеть и STAC отключены)"],
        }
    context = dict(state.get("context") or {})
    territories = context.get("territories") or []
    period = context.get("period") or {}
    analysis_type = str(context.get("analysis_type") or "vegetation_change")
    if not isinstance(territories, list):
        territories = []
    start_date = _parse_date(period.get("start")) if isinstance(period, dict) else None
    end_date = _parse_date(period.get("end")) if isinstance(period, dict) else None
    if not territories or start_date is None or end_date is None:
        return {"next_agent": "visualizer"}

    first = territories[0] if isinstance(territories[0], dict) else {}
    territory_name = str(first.get("name") or "Территория")
    bbox = await _resolve_bbox(first, territory_name)
    if bbox is None:
        return _fail(
            context,
            f"Не удалось определить границы территории «{territory_name}»",
        )

    threshold = float(context.get("ndvi_threshold") or DEFAULT_NDVI_THRESHOLD)
    min_area_m2 = float(context.get("min_area_m2") or DEFAULT_MIN_AREA_M2)

    start_scenes = await _search_period(bbox, start_date, "start_period")
    end_scenes = await _search_period(bbox, end_date, "end_period")
    if not start_scenes or not end_scenes:
        return _fail(
            context,
            "Нет пригодных сцен Sentinel-2 (облачность ≤20%) в окнах ±60 дней",
            {"accepted": [], "rejected": []},
        )

    try:
        start_result = await asyncio.wait_for(
            _period_composite(start_scenes, bbox),
            timeout=_COMPOSITE_TIMEOUT_SECONDS,
        )
        end_result = await asyncio.wait_for(
            _period_composite(end_scenes, bbox),
            timeout=_COMPOSITE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return _fail(
            context,
            "Таймаут чтения сцен Sentinel-2 (COG)",
            {"accepted": [], "rejected": []},
        )
    start_ndvi, start_transform, start_crs, start_ok, start_bad = start_result
    end_ndvi, _end_transform, _end_crs, end_ok, end_bad = end_result
    datasets = {
        "accepted": [*start_ok, *end_ok],
        "rejected": [*start_bad, *end_bad],
    }

    if start_ndvi is None or end_ndvi is None or start_transform is None:
        return _fail(context, "Не удалось построить NDVI-композит", datasets)
    if start_crs is None:
        return _fail(context, "Не удалось определить CRS сцен", datasets)
    if np.isnan(start_ndvi).all() or np.isnan(end_ndvi).all():
        return _fail(context, "В окне анализа нет валидных пикселей", datasets)

    rows = min(int(start_ndvi.shape[0]), int(end_ndvi.shape[0]))
    cols = min(int(start_ndvi.shape[1]), int(end_ndvi.shape[1]))
    start_ndvi = start_ndvi[:rows, :cols]
    end_ndvi = end_ndvi[:rows, :cols]

    valid_start = ~np.isnan(start_ndvi)
    valid_end = ~np.isnan(end_ndvi)
    shared_valid = valid_start & valid_end
    total_pixels = int(shared_valid.size)
    coverage = {
        "validStart": float(valid_start.mean()) if total_pixels else 0.0,
        "validEnd": float(valid_end.mean()) if total_pixels else 0.0,
        "sharedValid": float(shared_valid.mean()) if total_pixels else 0.0,
        "unknown": float((~shared_valid).mean()) if total_pixels else 1.0,
        "totalPixels": total_pixels,
    }
    actual_dates = {
        "start": _actual_dates(start_ok),
        "end": _actual_dates(end_ok),
    }
    start_scene_ids = [item.get("sceneId") for item in start_ok]
    end_scene_ids = [item.get("sceneId") for item in end_ok]
    shared_scene_ids = sorted(
        {str(item) for item in start_scene_ids if item}
        & {str(item) for item in end_scene_ids if item}
    )
    seasons = {
        "start": _season_for(start_ok[0].get("datetime")) if start_ok else None,
        "end": _season_for(end_ok[0].get("datetime")) if end_ok else None,
    }
    season_comparable = (
        seasons["start"] is not None and seasons["start"] == seasons["end"]
    )
    limitations: list[str] = []
    if not season_comparable:
        limitations.append(
            "Сезоны дат различаются "
            f"({seasons['start'] or '—'} и {seasons['end'] or '—'}): "
            "возможны сезонные изменения растительности."
        )
    if coverage["sharedValid"] < 0.5:
        limitations.append(
            "Общее валидное покрытие "
            f"{coverage['sharedValid'] * 100:.1f}%: результат может быть неполным."
        )
    diagnostics: dict[str, Any] = {
        "bbox": bbox,
        "requestedPeriod": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "actualDates": actual_dates,
        "sceneIds": {
            "start": start_scene_ids,
            "end": end_scene_ids,
        },
        "sceneOverlap": {
            "sharedSceneIds": shared_scene_ids,
            "startEndDisjoint": not shared_scene_ids,
        },
        "scenes": {"start": start_ok, "end": end_ok},
        "channels": {
            "red": "B04",
            "green": "B03",
            "blue": "B02",
            "nir": "B08",
            "scl": "SCL",
        },
        "classification": {
            "ndviThreshold": threshold,
            "minAreaM2": min_area_m2,
            "ndviFormula": "(B08 - B04) / (B08 + B04)",
        },
        "coverage": coverage,
        "seasons": {**seasons, "comparable": season_comparable},
        "transitionRule": (
            "retained: veg&veg; lost: veg&!veg; new: !veg&veg; "
            "unknown: невалидно хотя бы на одной дате"
        ),
        "limitations": limitations,
    }

    if coverage["sharedValid"] < MIN_VALID_COVERAGE:
        return _fail(
            context,
            "Недостаточное общее валидное покрытие "
            f"({coverage['sharedValid'] * 100:.1f}%) — изменения не рассчитываются",
            datasets,
            diagnostics,
        )

    start_class = classify_vegetation(start_ndvi, threshold)
    end_class = classify_vegetation(end_ndvi, threshold)
    retained, lost, new = detect_changes(
        start_class,
        end_class,
        valid_mask=shared_valid,
    )
    zones = build_change_zones(
        {"retained": retained, "lost": lost, "new": new},
        start_transform,
        start_crs,
        territory_name,
        min_area_m2,
    )
    if not zones:
        context.update(
            {
                "change_zones": [],
                "analysis_diagnostics": diagnostics,
                "analysis_sufficient": True,
                "datasets": datasets,
                "coverage": coverage,
                "aggregate": {
                    "has_overlap": False,
                    "note": "сумма по территориям (с возможным пересечением)",
                    "zonesCount": 0,
                    "retained": {"zonesCount": 0},
                    "lost": {"zonesCount": 0},
                    "new": {"zonesCount": 0},
                },
                "analysis_type": analysis_type,
                "ndvi_threshold": threshold,
                "min_area_m2": min_area_m2,
                "bbox": bbox,
            }
        )
        return {
            "context": context,
            "next_agent": "visualizer",
            "messages": ["Analyst: зоны изменений не обнаружены"],
        }

    counts = {
        change_class: sum(
            1 for zone in zones if zone["change_class"] == change_class
        )
        for change_class in _CHANGE_CLASSES
    }
    context.update(
        {
            "change_zones": zones,
            "analysis_diagnostics": diagnostics,
            "analysis_sufficient": True,
            "raster_refs": [
                {
                    "kind": "start_period",
                    "scene_ids": [item.get("sceneId") for item in start_ok],
                },
                {
                    "kind": "end_period",
                    "scene_ids": [item.get("sceneId") for item in end_ok],
                },
            ],
            "datasets": datasets,
            "aggregate": {
                "has_overlap": False,
                "note": "сумма по территориям (с возможным пересечением)",
                "zonesCount": len(zones),
                "retained": {"zonesCount": counts["retained"]},
                "lost": {"zonesCount": counts["lost"]},
                "new": {"zonesCount": counts["new"]},
            },
            "analysis_type": analysis_type,
            "ndvi_threshold": threshold,
            "min_area_m2": min_area_m2,
            "bbox": bbox,
            "coverage": coverage,
        }
    )
    return {
        "context": context,
        "next_agent": "visualizer",
        "messages": [f"Analyst: {len(zones)} зон изменений"],
    }


async def _read_scene_ndvi(
    scene: dict[str, Any],
    bbox: list[float],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Читает RED/NIR/SCL окно сцены и считает NDVI (SCL-маска)."""
    red_url = scene.get("red_url")
    nir_url = scene.get("nir_url")
    scl_url = scene.get("scl_url")
    if not (red_url and nir_url and scl_url):
        return None
    bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
    async with semaphore:
        try:
            red, transform = await asyncio.to_thread(
                read_cog_window,
                red_url,
                bbox_tuple,
                1,
                REFLECTANCE_SCALE,
            )
            if red.size == 0:
                return None
            out_shape = (int(red.shape[0]), int(red.shape[1]))
            nir, _ = await asyncio.to_thread(
                read_cog_window,
                nir_url,
                bbox_tuple,
                1,
                REFLECTANCE_SCALE,
                out_shape,
            )
            scl, _ = await asyncio.to_thread(
                read_cog_window,
                scl_url,
                bbox_tuple,
                1,
                SCL_SCALE,
                out_shape,
            )
        except Exception as exc:  # noqa: BLE001 — одна сцена не роняет анализ
            logger.warning(
                "Analyst: сцена %s не прочитана: %s",
                scene.get("id"),
                exc,
            )
            return None

    rows = min(red.shape[0], nir.shape[0], scl.shape[0])
    cols = min(red.shape[1], nir.shape[1], scl.shape[1])
    if rows <= 0 or cols <= 0:
        return None

    red_data = red[:rows, :cols].astype(np.float32)
    nir_data = nir[:rows, :cols].astype(np.float32)
    scl_data = scl[:rows, :cols]
    invalid = mask_scl(scl_data)
    red_data = np.where(invalid, np.nan, red_data)
    nir_data = np.where(invalid, np.nan, nir_data)
    ndvi = calculate_ndvi(red_data, nir_data)
    ndvi = np.where(invalid, np.nan, ndvi).astype(np.float32)

    crs: str | None = None
    proj_epsg = scene.get("proj_epsg")
    if proj_epsg is not None:
        try:
            crs = f"EPSG:{int(proj_epsg)}"
        except (TypeError, ValueError):
            crs = None

    return {
        "ndvi": ndvi,
        "transform": transform,
        "crs": crs,
        "scene": scene,
    }


async def _period_composite(
    scenes: list[dict[str, Any]],
    bbox: list[float],
) -> tuple[
    np.ndarray | None,
    Any,
    str | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Строит медианный NDVI-композит по сценам периода."""
    semaphore = asyncio.Semaphore(_BAND_READ_CONCURRENCY)
    results = await asyncio.gather(
        *(_read_scene_ndvi(scene, bbox, semaphore) for scene in scenes)
    )
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for scene, result in zip(scenes, results, strict=False):
        if result is None:
            rejected.append({**_scene_meta(scene), "reason": "read_error"})
        else:
            accepted.append(_scene_meta(scene))
            valid.append(result)

    if not valid:
        return None, None, None, accepted, rejected

    rows = min(int(item["ndvi"].shape[0]) for item in valid)
    cols = min(int(item["ndvi"].shape[1]) for item in valid)
    arrays = [item["ndvi"][:rows, :cols] for item in valid]
    composite = median_composite(arrays)
    first = valid[0]
    return composite, first["transform"], first["crs"], accepted, rejected


def _fail(
    context: dict[str, Any],
    message: str,
    datasets: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Возвращает управление Validator с понятной причиной и без зон."""
    context["change_zones"] = []
    if datasets is not None:
        context["datasets"] = datasets
    if diagnostics is not None:
        context["analysis_diagnostics"] = diagnostics
        context["coverage"] = diagnostics.get("coverage")
        context["analysis_sufficient"] = False
    context["analysis_error"] = message
    logger.warning("Analyst: %s", message)
    return {
        "context": context,
        "next_agent": "validator",
        "messages": [f"Analyst: {message}"],
    }