"""Analyst agent node (T059).

Uses stac_search for finding satellite imagery, raster_ops for NDVI calculation,
and landcover for detecting change zones.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import numpy as np
from rasterio.enums import Resampling

from glist.tools.change_zones import build_change_zones
from glist.tools.geocode import geocode_territory
from glist.tools.landcover import classify_vegetation, detect_changes
from glist.tools.raster_ops import (
    calculate_ndvi,
    grid_pixel_area_m2,
    mask_scl,
    median_composite,
    read_cog_window,
    reproject_to_grid,
)
from glist.tools.stac_search import search_sentinel2_scenes

logger = logging.getLogger("glist.agents.analyst")

DEFAULT_NDVI_THRESHOLD = 0.25
DEFAULT_MIN_AREA_M2 = 1000.0
MAX_CLOUD_COVER = 30
MAX_SCENES_PER_PERIOD = 5
COMPOSITE_WINDOW_DAYS = 30
EXPANDED_WINDOW_DAYS = 60
REFLECTANCE_SCALE = 0.0001
SCL_SCALE = 1.0
MAX_BBOX_SPAN_DEG = 0.12
MIN_VALID_COVERAGE = 0.1
# Рабочий параметр пригодности, НЕ подтверждённый критерий точности:
# ниже него результат считается технически рассчитанным, но ограниченным.
QUALITY_MIN_COVERAGE = 0.8
_MAX_SEASON_ATTEMPTS = 3
_SEASON_SEARCH_BUDGET_SECONDS = 90.0
_GRID_TOLERANCE_PIXELS = 1e-6
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
    "москв": [37.35, 55.55, 37.95, 55.95],
    "казан": [48.82, 55.60, 49.38, 55.93],
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
    lowered = name.lower()
    for key, static_bbox in _STATIC_BBOXES.items():
        if key in lowered:
            logger.info(
                "Analyst: использую статический bbox для территории %s",
                name,
            )
            return _limit_bbox(static_bbox)
    geocoded = await geocode_territory(name)
    payload_bbox = _bbox_from_payload(geocoded)
    if payload_bbox is not None:
        return _limit_bbox(payload_bbox)
    return None


async def _search_center(
    bbox: list[float],
    center: date,
    role: str,
    bounds: tuple[date, date] | None = None,
) -> list[dict[str, Any]]:
    """Ищет сцены в окне ±30 дней, при пустом результате — ±60 дней.

    ``bounds`` ограничивает окно поиска пределами исходного запроса: подбор
    сопоставимого сезона не должен выходить за запрошенный период.
    """
    for days in (COMPOSITE_WINDOW_DAYS, EXPANDED_WINDOW_DAYS):
        start, end = _window(center, days)
        if bounds is not None:
            start = max(start, bounds[0].isoformat())
            end = min(end, bounds[1].isoformat())
            if start > end:
                continue
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
        center_season = _SEASON_BY_MONTH.get(center.month)
        same_season = [
            scene
            for scene in scenes
            if _season_for(scene.get("datetime")) == center_season
        ]
        pool = same_season or scenes
        selected = pool[:MAX_SCENES_PER_PERIOD]
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


def _clamp_date(year: int, month: int, day: int) -> date | None:
    """Безопасно строит дату (например, 29 февраля)."""
    max_day = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, min(day, max_day))
    except ValueError:
        return None


def _season_candidates(
    start_date: date,
    end_date: date,
) -> list[tuple[date, date, int, int]]:
    """Кандидаты сезонных окон внутри запроса (одинаковый сезон разных лет)."""
    anchors = [
        (end_date.month, end_date.day),
        (start_date.month, start_date.day),
        (4, 15),
        (7, 15),
        (10, 1),
        (1, 15),
    ]
    candidates: list[tuple[date, date, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for month, day in anchors:
        if (month, day) in seen:
            continue
        seen.add((month, day))
        candidate_start = _clamp_date(start_date.year, month, day)
        candidate_end = _clamp_date(end_date.year, month, day)
        if candidate_start is None or candidate_end is None:
            continue
        if candidate_start < start_date or candidate_end > end_date:
            continue
        if candidate_start >= candidate_end:
            continue
        candidates.append((candidate_start, candidate_end, month, day))
    return candidates


def _period_season(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Сезон набора сцен: единый сезон или ``None`` при смешении."""
    seasons: list[str] = []
    for item in items:
        season = _season_for(item.get("datetime"))
        if season is not None and season not in seasons:
            seasons.append(season)
    return {
        "season": seasons[0] if len(seasons) == 1 else None,
        "seasons": seasons,
        "consistent": len(seasons) == 1,
    }


def _seasons_comparable(seasons: dict[str, Any]) -> bool:
    """True, если обе даты укладываются в один и тот же сезон."""
    start = seasons.get("start")
    end = seasons.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return False
    if not (start.get("consistent") and end.get("consistent")):
        return False
    start_season = start.get("season")
    return bool(start_season) and start_season == end.get("season")


async def _search_comparable_periods(
    bbox: list[float],
    start_date: date,
    end_date: date,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Сначала запрошенные даты; при разных сезонах — сопоставимое окно.

    Запрошенные даты не подменяются скрытно: информация о методе, сдвиге
    сезона и фактических центрах возвращается в диагностику.
    """
    requested_start = await _search_center(bbox, start_date, "start_period")
    requested_end = await _search_center(bbox, end_date, "end_period")
    requested_seasons = {
        "start": _period_season(requested_start),
        "end": _period_season(requested_end),
    }
    info: dict[str, Any] = {
        "method": "requested",
        "seasonAdjusted": False,
        "requestedPeriod": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "requestedCenters": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "usedCenters": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "requestedSceneDates": {
            "start": _actual_dates(requested_start),
            "end": _actual_dates(requested_end),
        },
        "usedSceneDates": {
            "start": _actual_dates(requested_start),
            "end": _actual_dates(requested_end),
        },
        "requestedSeasons": requested_seasons,
        "usedSeasons": requested_seasons,
        "attempted": [],
    }
    if not requested_start or not requested_end:
        info["method"] = "requested_missing"
        return requested_start, requested_end, info
    if _seasons_comparable(requested_seasons):
        return requested_start, requested_end, info

    attempted: list[dict[str, Any]] = []
    started = time.monotonic()
    for (
        candidate_start,
        candidate_end,
        month,
        day,
    ) in _season_candidates(start_date, end_date):
        if len(attempted) >= _MAX_SEASON_ATTEMPTS:
            break
        if time.monotonic() - started > _SEASON_SEARCH_BUDGET_SECONDS:
            info["budgetExhausted"] = True
            break
        if candidate_start == start_date and candidate_end == end_date:
            continue
        start_scenes = (
            requested_start
            if candidate_start == start_date
            else await _search_center(
                bbox,
                candidate_start,
                "start_period",
                (start_date, end_date),
            )
        )
        end_scenes = (
            requested_end
            if candidate_end == end_date
            else await _search_center(
                bbox,
                candidate_end,
                "end_period",
                (start_date, end_date),
            )
        )
        used_seasons = {
            "start": _period_season(start_scenes),
            "end": _period_season(end_scenes),
        }
        attempted.append(
            {
                "anchorMonth": month,
                "anchorDay": day,
                "startCenter": candidate_start.isoformat(),
                "endCenter": candidate_end.isoformat(),
                "startSeason": used_seasons["start"].get("season"),
                "endSeason": used_seasons["end"].get("season"),
                "startSeasons": used_seasons["start"].get("seasons"),
                "endSeasons": used_seasons["end"].get("seasons"),
                "startScenes": len(start_scenes),
                "endScenes": len(end_scenes),
                "startDates": _actual_dates(start_scenes),
                "endDates": _actual_dates(end_scenes),
            }
        )
        if start_scenes and end_scenes and _seasons_comparable(used_seasons):
            info.update(
                {
                    "method": "same_season",
                    "seasonAdjusted": True,
                    "anchorMonth": month,
                    "anchorDay": day,
                    "usedCenters": {
                        "start": candidate_start.isoformat(),
                        "end": candidate_end.isoformat(),
                    },
                    "usedSeasons": used_seasons,
                    "usedSceneDates": {
                        "start": _actual_dates(start_scenes),
                        "end": _actual_dates(end_scenes),
                    },
                    "attempted": attempted,
                }
            )
            return start_scenes, end_scenes, info
    info["method"] = "requested_season_mismatch"
    info["attempted"] = attempted
    return requested_start, requested_end, info


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
        "redScale": scene.get("red_scale"),
        "redOffset": scene.get("red_offset"),
        "greenScale": scene.get("green_scale"),
        "greenOffset": scene.get("green_offset"),
        "blueScale": scene.get("blue_scale"),
        "blueOffset": scene.get("blue_offset"),
        "nirScale": scene.get("nir_scale"),
        "nirOffset": scene.get("nir_offset"),
        "sclScale": scene.get("scl_scale"),
        "sclOffset": scene.get("scl_offset"),
        "boaOffsetDn": scene.get("boa_offset_dn"),
        "boaOffsetApplied": scene.get("boa_offset_applied"),
    }


def _read_band_window(
    url: str,
    bbox: tuple[float, float, float, float],
    scale: float,
    out_shape: tuple[int, int] | None,
    offset: float,
) -> tuple[np.ndarray, Any]:
    """Читает окно band; offset передаётся только при ненулевой коррекции.

    ``offset`` — в единицах результата (``value = dn * scale + offset``).
    """
    if offset:
        return read_cog_window(
            url,
            bbox,
            1,
            scale,
            out_shape,
            offset=offset,
        )
    return read_cog_window(url, bbox, 1, scale, out_shape)


def _band_scaling(
    scene: dict[str, Any],
    prefix: str,
    default_scale: float,
) -> tuple[float, float, str]:
    """Возвращает коррекцию band в семантике STAC ``dn * scale + offset``.

    Источники (по приоритету):
    1. ``raster:bands`` STAC — ``scale``/``offset`` уже в единицах результата;
    2. ``BOA_ADD_OFFSET`` в DN — явно приводится к единицам результата
       ``offset = boa_dn * scale`` (``source="boa_converted"``);
    3. значение по умолчанию для Sentinel-2 L2A (``scale=0.0001, offset=0``).
    """
    raw_scale = scene.get(f"{prefix}_scale")
    raw_offset = scene.get(f"{prefix}_offset")
    if isinstance(raw_scale, (int, float)) and float(raw_scale) > 0:
        offset = float(raw_offset) if isinstance(raw_offset, (int, float)) else 0.0
        return float(raw_scale), offset, "stac"
    boa_dn = scene.get("boa_offset_dn")
    if isinstance(boa_dn, (int, float)) and float(boa_dn) != 0.0:
        scale = float(default_scale)
        return scale, float(boa_dn) * scale, "boa_converted"
    return float(default_scale), 0.0, "default"


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
        return _fail(
            context,
            "Не удалось определить территорию и период запроса: "
            "Researcher не вернул обязательные параметры",
            stage="input_validation",
        )

    first = territories[0] if isinstance(territories[0], dict) else {}
    territory_name = str(first.get("name") or "Территория")
    bbox = await _resolve_bbox(first, territory_name)
    if bbox is None:
        return _fail(
            context,
            f"Не удалось определить границы территории «{territory_name}»",
            stage="geocoding",
        )

    threshold = float(context.get("ndvi_threshold") or DEFAULT_NDVI_THRESHOLD)
    min_area_m2 = float(context.get("min_area_m2") or DEFAULT_MIN_AREA_M2)

    start_scenes, end_scenes, period_selection = await _search_comparable_periods(
        bbox, start_date, end_date
    )
    if not start_scenes or not end_scenes:
        return _fail(
            context,
            "Нет пригодных сцен Sentinel-2 (облачность ≤20%) в окнах ±60 дней",
            {"accepted": [], "rejected": []},
            {
                "bbox": bbox,
                "requestedPeriod": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "periodSelection": period_selection,
                "actualDates": {"start": [], "end": []},
            },
            stage="stac_search",
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
            {
                "bbox": bbox,
                "requestedPeriod": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "periodSelection": period_selection,
                "actualDates": {"start": [], "end": []},
            },
            stage="scene_read",
        )
    start_ndvi, start_grid, start_ok, start_bad = start_result
    end_ndvi, end_grid, end_ok, end_bad = end_result
    datasets = {
        "accepted": [*start_ok, *end_ok],
        "rejected": [*start_bad, *end_bad],
    }
    actual_dates = {
        "start": _actual_dates(start_ok),
        "end": _actual_dates(end_ok),
    }
    period_diagnostics: dict[str, Any] = {
        "bbox": bbox,
        "requestedPeriod": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "periodSelection": period_selection,
        "actualDates": actual_dates,
        "sceneIds": {
            "start": [item.get("sceneId") for item in start_ok],
            "end": [item.get("sceneId") for item in end_ok],
        },
        "scenes": {"start": start_ok, "end": end_ok},
    }

    if (
        start_ndvi is None
        or end_ndvi is None
        or start_grid is None
        or end_grid is None
    ):
        return _fail(
            context,
            "Не удалось построить NDVI-композит",
            datasets,
            period_diagnostics,
            stage="scene_read",
        )
    if start_grid["crs"] is None or end_grid["crs"] is None:
        return _fail(
            context,
            "Не удалось определить CRS сцен",
            datasets,
            period_diagnostics,
            stage="scene_read",
        )
    if np.isnan(start_ndvi).all() or np.isnan(end_ndvi).all():
        return _fail(
            context,
            "В окне анализа нет валидных пикселей",
            datasets,
            period_diagnostics,
            stage="scene_read",
        )

    start_transform = start_grid["transform"]
    start_crs = start_grid["crs"]
    grid_check = _grid_differences(start_grid["info"], end_grid["info"])
    grid_alignment = "none"
    if not grid_check["compatibleWithoutResampling"]:
        end_ndvi = await asyncio.to_thread(
            reproject_to_grid,
            end_ndvi,
            src_transform=end_grid["transform"],
            src_crs=end_grid["crs"],
            dst_transform=start_transform,
            dst_crs=start_crs,
            dst_shape=start_grid["shape"],
            resampling=Resampling.bilinear,
        )
        grid_alignment = "end_reprojected_to_start"
    rows, cols = start_grid["shape"]
    start_ndvi = start_ndvi[:rows, :cols]
    end_ndvi = end_ndvi[:rows, :cols]

    valid_start = ~np.isnan(start_ndvi)
    valid_end = ~np.isnan(end_ndvi)
    shared_valid = valid_start & valid_end
    total_pixels = int(shared_valid.size)
    shared_share = float(shared_valid.mean()) if total_pixels else 0.0
    unknown_share = float((~shared_valid).mean()) if total_pixels else 1.0
    center_lat = (float(bbox[1]) + float(bbox[3])) / 2
    pixel_area_m2 = grid_pixel_area_m2(start_transform, start_crs, center_lat)
    shared_pixels = int(shared_valid.sum())
    unknown_pixels = total_pixels - shared_pixels
    coverage = {
        "validStart": float(valid_start.mean()) if total_pixels else 0.0,
        "validEnd": float(valid_end.mean()) if total_pixels else 0.0,
        "sharedValid": shared_share,
        "unknown": unknown_share,
        "totalPixels": total_pixels,
        "sharedValidPixels": shared_pixels,
        "unknownPixels": unknown_pixels,
        "pixelAreaM2": pixel_area_m2,
        "analysisAreaKm2": (
            total_pixels * pixel_area_m2 / 1_000_000.0
            if pixel_area_m2 is not None
            else None
        ),
        "sharedValidAreaKm2": (
            shared_pixels * pixel_area_m2 / 1_000_000.0
            if pixel_area_m2 is not None
            else None
        ),
        "unknownAreaKm2": (
            unknown_pixels * pixel_area_m2 / 1_000_000.0
            if pixel_area_m2 is not None
            else None
        ),
        "areaNote": (
            "Площадь покрытия рассчитана по пикселям сетки расчёта; "
            "площади зон считает PostGIS после фильтрации min_area."
        ),
    }
    start_scene_ids = [item.get("sceneId") for item in start_ok]
    end_scene_ids = [item.get("sceneId") for item in end_ok]
    shared_scene_ids = sorted(
        {str(item) for item in start_scene_ids if item}
        & {str(item) for item in end_scene_ids if item}
    )
    start_period_season = _period_season(start_ok)
    end_period_season = _period_season(end_ok)
    seasons = {
        "start": start_period_season["season"],
        "end": end_period_season["season"],
    }
    season_comparable = _seasons_comparable(
        {"start": start_period_season, "end": end_period_season}
    )
    season_adjusted = bool(period_selection.get("seasonAdjusted"))
    limitations: list[str] = []
    if season_adjusted:
        limitations.append(
            "Запрошенные даты "
            f"({start_date.isoformat()}…{end_date.isoformat()}) заменены "
            "сопоставимым сезонным окном внутри запроса: фактические даты — "
            f"{', '.join(actual_dates['start']) or '—'} и "
            f"{', '.join(actual_dates['end']) or '—'}."
        )
    if not season_comparable:
        limitations.append(
            "Сезоны дат различаются "
            f"({seasons['start'] or '—'} и {seasons['end'] or '—'}): "
            "возможны сезонные изменения растительности."
        )
    if shared_share < QUALITY_MIN_COVERAGE:
        limitations.append(
            "Общее валидное покрытие "
            f"{shared_share * 100:.1f}% ниже рабочего порога "
            f"{QUALITY_MIN_COVERAGE * 100:.0f}% — интерпретация изменений "
            "ограничена (порог не подтверждён измерением точности)."
        )
    elif shared_share < 0.5:
        limitations.append(
            "Общее валидное покрытие "
            f"{shared_share * 100:.1f}%: результат может быть неполным."
        )
    if pixel_area_m2 is None:
        limitations.append(
            "Площадь покрытия не рассчитана: неизвестные единицы CRS."
        )
    if grid_alignment != "none":
        limitations.append(
            "Сетка конца приведена к сетке начала: "
            + "; ".join(str(item) for item in grid_check["reasons"])
            + "."
        )

    quality_reasons: list[str] = []
    if shared_share < QUALITY_MIN_COVERAGE:
        quality_reasons.append(
            "Общее валидное покрытие "
            f"{shared_share * 100:.1f}% < "
            f"{QUALITY_MIN_COVERAGE * 100:.0f}%."
        )
    if not season_comparable:
        quality_reasons.append("Сезоны дат не сопоставимы.")
    interpretability = {
        "status": "sufficient" if not quality_reasons else "limited",
        "reasons": quality_reasons,
        "sharedValidCoverage": shared_share,
        "minCoverageThreshold": QUALITY_MIN_COVERAGE,
        "thresholdNote": (
            "Порог покрытия — рабочий параметр, а не подтверждённый критерий "
            "точности классификации."
        ),
    }
    analysis_sufficient = not quality_reasons

    diagnostics: dict[str, Any] = {
        "bbox": bbox,
        "requestedPeriod": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "periodSelection": period_selection,
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
        "scaling": {
            "red": (start_grid.get("scaling") or {}).get("red"),
            "nir": (start_grid.get("scaling") or {}).get("nir"),
            "semantics": (
                "value = DN * scale + offset (STAC/GDAL); offset — в единицах "
                "отражательной способности, не в DN"
            ),
            "boaConversion": (
                "BOA_ADD_OFFSET в DN приводится явно: offset = boa_dn * scale"
            ),
            "note": (
                "Коррекция применяется один раз; объявленные в растре "
                "scale/offset имеют приоритет над значениями по умолчанию."
            ),
        },
        "grid": {
            "start": start_grid["info"],
            "end": end_grid["info"],
            "check": grid_check,
            "alignment": grid_alignment,
            "note": "Пиксельное сравнение выполняется на сетке начала.",
        },
        "classification": {
            "ndviThreshold": threshold,
            "minAreaM2": min_area_m2,
            "ndviFormula": "(B08 - B04) / (B08 + B04)",
        },
        "coverage": coverage,
        "unknown": {
            "class": "unknown",
            "reason": "NoData/облака/тени/снег хотя бы на одной дате",
            "share": unknown_share,
            "areaKm2": coverage["unknownAreaKm2"],
        },
        "seasons": {
            **seasons,
            "comparable": season_comparable,
            "seasonAdjusted": season_adjusted,
            "startDetails": start_period_season,
            "endDetails": end_period_season,
        },
        "technicalSuccess": True,
        "interpretability": interpretability,
        "transitionRule": (
            "retained: veg&veg; lost: veg&!veg; new: !veg&veg; "
            "unknown: невалидно хотя бы на одной дате"
        ),
        "limitations": limitations,
    }

    if shared_share < MIN_VALID_COVERAGE:
        return _fail(
            context,
            "Недостаточное общее валидное покрытие "
            f"({shared_share * 100:.1f}%) — изменения не рассчитываются",
            datasets,
            diagnostics,
            category="data_quality",
            stage="coverage",
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
                "analysis_sufficient": analysis_sufficient,
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
            "analysis_sufficient": analysis_sufficient,
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
    red_scale, red_offset, red_source = _band_scaling(
        scene, "red", REFLECTANCE_SCALE
    )
    nir_scale, nir_offset, nir_source = _band_scaling(
        scene, "nir", REFLECTANCE_SCALE
    )
    scl_scale, scl_offset, scl_source = _band_scaling(scene, "scl", SCL_SCALE)
    async with semaphore:
        try:
            red, transform = await asyncio.to_thread(
                _read_band_window,
                red_url,
                bbox_tuple,
                red_scale,
                None,
                red_offset,
            )
            if red.size == 0:
                return None
            out_shape = (int(red.shape[0]), int(red.shape[1]))
            nir, _ = await asyncio.to_thread(
                _read_band_window,
                nir_url,
                bbox_tuple,
                nir_scale,
                out_shape,
                nir_offset,
            )
            scl, _ = await asyncio.to_thread(
                _read_band_window,
                scl_url,
                bbox_tuple,
                scl_scale,
                out_shape,
                scl_offset,
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
        "scaling": {
            "red": {"scale": red_scale, "offset": red_offset, "source": red_source},
            "nir": {"scale": nir_scale, "offset": nir_offset, "source": nir_source},
            "scl": {"scale": scl_scale, "offset": scl_offset, "source": scl_source},
        },
    }


def _grid_public(
    transform: Any,
    crs: str | None,
    shape: tuple[int, int],
) -> dict[str, Any]:
    """Описание сетки растра для диагностики."""
    return {
        "crs": crs,
        "shape": [int(shape[0]), int(shape[1])],
        "resolution": [abs(float(transform.a)), abs(float(transform.e))],
        "origin": [float(transform.c), float(transform.f)],
        "transform": [float(value) for value in tuple(transform)[:6]],
    }


def _grid_differences(
    start: dict[str, Any],
    end: dict[str, Any],
) -> dict[str, Any]:
    """Сравнивает CRS/transform/shape/разрешение сеток двух дат."""
    reasons: list[str] = []
    crs_equal = start.get("crs") == end.get("crs")
    if not crs_equal:
        reasons.append("CRS сеток различается")
    start_res = start.get("resolution") or [0.0, 0.0]
    end_res = end.get("resolution") or [0.0, 0.0]
    resolution_equal = all(
        abs(float(a) - float(b)) <= max(abs(float(a)), 1e-12) * 1e-6
        for a, b in zip(start_res, end_res, strict=False)
    )
    if not resolution_equal:
        reasons.append("Разрешение сеток различается")
    shape_equal = start.get("shape") == end.get("shape")
    if not shape_equal:
        reasons.append("Размер сеток различается")
    start_origin = start.get("origin") or [0.0, 0.0]
    end_origin = end.get("origin") or [0.0, 0.0]
    res_x = max(abs(float(start_res[0])), 1e-12)
    res_y = max(abs(float(start_res[1])), 1e-12)
    origin_shift = max(
        abs(float(end_origin[0]) - float(start_origin[0])) / res_x,
        abs(float(end_origin[1]) - float(start_origin[1])) / res_y,
    )
    if origin_shift > _GRID_TOLERANCE_PIXELS:
        reasons.append("Начало сеток смещено")
    return {
        "crsEqual": crs_equal,
        "resolutionEqual": resolution_equal,
        "shapeEqual": shape_equal,
        "originShiftPixels": origin_shift,
        "compatibleWithoutResampling": not reasons,
        "reasons": reasons,
    }


async def _period_composite(
    scenes: list[dict[str, Any]],
    bbox: list[float],
) -> tuple[
    np.ndarray | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Строит медианный NDVI-композит и проверяет единую сетку периода."""
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
            valid.append(result)

    if not valid:
        return None, None, accepted, rejected

    reference = valid[0]
    reference_transform = reference["transform"]
    reference_res = (
        abs(float(reference_transform.a)),
        abs(float(reference_transform.e)),
    )
    compatible: list[dict[str, Any]] = [reference]
    for item in valid[1:]:
        transform = item["transform"]
        resolution = (abs(float(transform.a)), abs(float(transform.e)))
        resolution_ok = all(
            abs(a - b) <= max(abs(a), 1e-12) * 1e-6
            for a, b in zip(reference_res, resolution, strict=False)
        )
        if item["crs"] != reference["crs"]:
            rejected.append(
                {
                    **_scene_meta(item["scene"]),
                    "reason": "grid_crs_mismatch",
                }
            )
            continue
        if not resolution_ok:
            rejected.append(
                {
                    **_scene_meta(item["scene"]),
                    "reason": "grid_resolution_mismatch",
                }
            )
            continue
        compatible.append(item)

    rows = min(int(item["ndvi"].shape[0]) for item in compatible)
    cols = min(int(item["ndvi"].shape[1]) for item in compatible)
    if rows <= 0 or cols <= 0:
        return None, None, accepted, rejected
    # accepted = только сцены, реально вошедшие в композит (их видит preview).
    accepted = [_scene_meta(item["scene"]) for item in compatible]
    arrays = [item["ndvi"][:rows, :cols] for item in compatible]
    composite = median_composite(arrays)
    grid = {
        "transform": reference_transform,
        "crs": reference["crs"],
        "shape": (rows, cols),
        "info": _grid_public(
            reference_transform,
            reference["crs"],
            (rows, cols),
        ),
        "scaling": reference.get("scaling"),
    }
    return composite, grid, accepted, rejected


def _fail(
    context: dict[str, Any],
    message: str,
    datasets: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    *,
    category: str = "analysis_error",
    stage: str = "analysis",
) -> dict[str, Any]:
    """Возвращает управление Validator с причиной и без зон.

    ``category="analysis_error"`` — технический сбой (чтение/поиск/таймаут):
    качество спутниковых данных при этом НЕ оценивается. Недостаточное
    покрытие или несопоставимость сезонов передаются как ``data_quality``.
    """
    context["change_zones"] = []
    if datasets is not None:
        context["datasets"] = datasets
    technical = category == "analysis_error"
    failure = {
        "category": category,
        "stage": stage,
        "message": message,
        "technicalSuccess": not technical,
        "dataQualityAssessed": not technical,
    }
    merged = dict(diagnostics or {})
    merged.setdefault("technicalSuccess", not technical)
    merged.setdefault("failure", failure)
    if technical:
        merged.setdefault(
            "interpretability",
            {
                "status": "not_evaluated",
                "reasons": [
                    "Технический сбой: пригодность данных не оценивалась."
                ],
            },
        )
    context["analysis_diagnostics"] = merged
    context["coverage"] = merged.get("coverage")
    context["analysis_failure"] = failure
    context["analysis_sufficient"] = False
    context["analysis_error"] = message
    logger.warning("Analyst: %s", message)
    return {
        "context": context,
        "next_agent": "validator",
        "messages": [f"Analyst: {message}"],
    }