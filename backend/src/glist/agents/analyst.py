"""Analyst agent node (T059).

Uses stac_search for finding satellite imagery, raster_ops for NDVI calculation,
and landcover for detecting change zones.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import math
import os
import time
from datetime import date, timedelta
from typing import Any

import numpy as np
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform_bounds, transform_geom

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
MAX_BBOX_SPAN_DEG = 0.7
MIN_VALID_COVERAGE = 0.1
# Рабочий параметр пригодности, НЕ подтверждённый критерий точности:
# ниже него результат считается технически рассчитанным, но ограниченным.
QUALITY_MIN_COVERAGE = 0.8
_MAX_SEASON_ATTEMPTS = 3
_SEASON_SEARCH_BUDGET_SECONDS = 90.0
_GRID_TOLERANCE_PIXELS = 1e-6
_BAND_READ_CONCURRENCY = 4
_COMPOSITE_TIMEOUT_SECONDS = 900.0
ANALYSIS_CHUNK_SPAN_DEG = 0.3
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


def _bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    """Прямоугольник bbox как GeoJSON-полигон (fallback границы)."""
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


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


async def _resolve_territory_aoi(
    territory: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    """Выбирает AOI с учётом района и города (без подмены район→город)."""
    parent_raw = territory.get("parent")
    parent = (
        parent_raw.strip()
        if isinstance(parent_raw, str) and parent_raw.strip()
        else None
    )
    requested = {
        "name": name,
        "parent": parent,
        "type": territory.get("type"),
    }

    def failure(
        message: str,
        stage: str,
        source: str,
        candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "bbox": None,
            "geometry": None,
            "error": message,
            "stage": stage,
            "source": source,
            "requestedTerritory": requested,
            "candidates": candidates or [],
        }

    if parent is None and territory.get("type") == "district":
        return failure(
            f"Для района «{name}» не указан город. Уточните запрос.",
            "territory_selection",
            "district_without_parent",
        )

    geometry: dict[str, Any] | None = None
    raw_geometry = territory.get("geometry")
    if isinstance(raw_geometry, dict):
        geometry = raw_geometry
    values = _bbox_from_payload({"bbox": territory.get("bbox")})

    if values is not None and geometry is not None:
        # Пользователь уже подтвердил территорию: используем ровно эту
        # геометрию, без повторного геокодинга и обрезки.
        return {
            "bbox": values,
            "geometry": geometry,
            "error": None,
            "stage": "ok",
            "source": str(territory.get("source") or "confirmed"),
            "requestedTerritory": requested,
            "displayName": territory.get("displayName"),
            "addresstype": territory.get("addresstype"),
            "candidates": [],
        }

    geocoded: dict[str, Any] | None = None
    if values is None or geometry is None:
        try:
            geocoded = await geocode_territory(
                name,
                parent=parent,
                object_type=territory.get("type"),
            )
        except Exception as exc:  # noqa: BLE001 — сеть/HTTP/разбор
            return failure(
                f"Геокодер недоступен или ограничил запросы ({exc}). "
                "Можно выбрать участок на карте вручную.",
                "territory_selection",
                "geocoder_error",
            )
    if values is None and geocoded is not None:
        values = _bbox_from_payload(geocoded)
    if values is None and parent is not None:
        return failure(
            f"Район «{name}» в «{parent}» не найден. Уточните название "
            "или выберите участок на карте.",
            "territory_selection",
            "district_not_found",
        )
    if values is None:
        return failure(
            f"Не удалось определить границы территории «{name}»",
            "geocoding",
            "geocoding",
        )

    source = "geocoding"
    candidates: list[dict[str, Any]] = []
    display_name: str | None = None
    if parent is not None:
        if geocoded is None:
            return failure(
                f"Район «{name}» в «{parent}» не найден.",
                "territory_selection",
                "district_not_found",
            )
        display_name = str(
            geocoded.get("displayName") or geocoded.get("name") or ""
        )
        if parent.lower().replace("ё", "е") not in display_name.lower().replace(
            "ё", "е"
        ):
            return failure(
                f"Геокодер вернул объект вне «{parent}»: {display_name}. "
                "Уточните запрос или выберите участок на карте.",
                "territory_selection",
                "district_parent_mismatch",
            )
        candidates = [
            item
            for item in (geocoded.get("candidates") or [])
            if isinstance(item, dict) and item.get("bbox")
        ][:8]
        if geocoded.get("ambiguous"):
            names = "; ".join(
                str(item.get("displayName") or item.get("name"))
                for item in candidates[:5]
            )
            return failure(
                f"Найдено несколько объектов: {names}. Выберите нужный.",
                "territory_selection",
                "ambiguous",
                candidates,
            )
        geometry = geocoded.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in (
            "Polygon",
            "MultiPolygon",
        ):
            return failure(
                f"Граница района «{name}» не найдена. Выберите участок на карте.",
                "territory_selection",
                "district_boundary_missing",
                candidates,
            )
        values = _bbox_from_payload(geocoded) or values
        source = "geocoding+polygon"
    else:
        if geometry is None and geocoded is not None:
            candidate = geocoded.get("geometry")
            if isinstance(candidate, dict) and candidate.get("type") in (
                "Polygon",
                "MultiPolygon",
            ):
                geometry = candidate
                display_name = geocoded.get("displayName")
        if geometry is None:
            geometry = _bbox_polygon(values)
            source = "geocoding+bbox_polygon"
        else:
            source = "geocoding+polygon"

    west, south, east, north = values
    if east - west > MAX_BBOX_SPAN_DEG or north - south > MAX_BBOX_SPAN_DEG:
        return {
            "bbox": values,
            "geometry": geometry,
            "error": (
                f"Территория «{name}» превышает лимит анализа "
                f"({east - west:.2f}°×{north - south:.2f}° при максимуме "
                f"{MAX_BBOX_SPAN_DEG:.2f}°). Выберите участок."
            ),
            "stage": "aoi_size",
            "source": source,
            "requestedTerritory": requested,
            "candidates": candidates,
        }
    return {
        "bbox": values,
        "geometry": geometry,
        "error": None,
        "stage": "ok",
        "source": source,
        "requestedTerritory": requested,
        "displayName": display_name,
        "addresstype": geocoded.get("addresstype") if geocoded else None,
        "candidates": candidates,
    }


def _chunk_bboxes(
    bbox: list[float],
    span_deg: float,
) -> list[list[float]]:
    """Делит bbox на участки не больше ``span_deg`` (память и время)."""
    west, south, east, north = bbox
    lon_steps = max(1, math.ceil((east - west) / span_deg))
    lat_steps = max(1, math.ceil((north - south) / span_deg))
    step_lon = (east - west) / lon_steps
    step_lat = (north - south) / lat_steps
    chunks: list[list[float]] = []
    for i in range(lon_steps):
        for j in range(lat_steps):
            chunks.append(
                [
                    west + i * step_lon,
                    south + j * step_lat,
                    west + (i + 1) * step_lon,
                    south + (j + 1) * step_lat,
                ]
            )
    return chunks


def _aoi_mask(
    geometry: dict[str, Any] | None,
    transform: Any,
    shape: tuple[int, int],
    crs: str | None,
) -> np.ndarray:
    """Растеризует границу территории на сетку анализа (CRS растра)."""
    if not isinstance(geometry, dict):
        return np.ones(shape, dtype=bool)
    target_geometry = geometry
    if crs is not None and crs != "EPSG:4326":
        try:
            target_geometry = transform_geom("EPSG:4326", crs, geometry)
        except Exception:  # noqa: BLE001 — без маски лучше, чем упасть
            logger.warning("Analyst: не удалось перепроецировать AOI в %s", crs)
            return np.ones(shape, dtype=bool)
    burned = rasterize(
        [(target_geometry, 1)],
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    return np.asarray(burned, dtype=bool)


async def _search_center(
    bbox: list[float],
    center: date,
    role: str,
    bounds: tuple[date, date] | None = None,
) -> list[dict[str, Any]]:
    """Ищет сцены в окне ±60 дней и оставляет тот же сезон.

    Широкое окно нужно для покрытия: в облачный месяц ±30 даёт мало пригодных
    пикселей. ``bounds`` ограничивает окно пределами запроса.
    """
    start, end = _window(center, EXPANDED_WINDOW_DAYS)
    if bounds is not None:
        start = max(start, bounds[0].isoformat())
        end = min(end, bounds[1].isoformat())
        if start > end:
            return []
    scenes = await search_sentinel2_scenes(
        bbox,
        start,
        end,
        max_cloud_cover=MAX_CLOUD_COVER,
        limit=20,
    )
    if not scenes:
        return []
    for scene in scenes:
        parsed = _parse_date(scene.get("datetime"))
        delta = abs((parsed - center).days) if parsed is not None else 60
        scene["window_days"] = (
            COMPOSITE_WINDOW_DAYS if delta <= COMPOSITE_WINDOW_DAYS else
            EXPANDED_WINDOW_DAYS
        )
    ordered = sorted(
        scenes,
        key=lambda scene: float(scene.get("cloud_cover") or 100),
    )
    center_season = _SEASON_BY_MONTH.get(center.month)
    same_season = [
        scene
        for scene in ordered
        if _season_for(scene.get("datetime")) == center_season
    ]
    pool = same_season or ordered
    selected = pool[:MAX_SCENES_PER_PERIOD]
    for scene in selected:
        scene["role"] = role
    logger.info(
        "Analyst: %s — %d сцен (кандидатов %d, окно ±%d дней)",
        role,
        len(selected),
        len(scenes),
        EXPANDED_WINDOW_DAYS,
    )
    return selected


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
        "windowDays": scene.get("window_days"),
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
    aoi = await _resolve_territory_aoi(first, territory_name)
    bbox = aoi.get("bbox")
    aoi_geometry = aoi.get("geometry")
    aoi_source = str(aoi.get("source") or "geocoding")
    territory_display = aoi.get("displayName")
    territory_info = {
        "requested": aoi.get("requestedTerritory"),
        "displayName": territory_display,
        "addresstype": aoi.get("addresstype"),
        "source": aoi_source,
        "candidates": aoi.get("candidates") or [],
    }
    if bbox is None:
        return _fail(
            context,
            str(aoi.get("error") or "Не удалось определить территорию анализа"),
            diagnostics={
                "territorySelection": territory_info,
                "requestedTerritory": aoi.get("requestedTerritory"),
                "candidates": aoi.get("candidates") or [],
            },
            stage=str(aoi.get("stage") or "territory_selection"),
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

    chunk_records: list[dict[str, Any]] = []
    accepted_by_key: dict[str, dict[str, Any]] = {}
    rejected_by_key: dict[str, dict[str, Any]] = {}
    failed_chunks: list[list[float]] = []
    for chunk_bbox in _chunk_bboxes(bbox, ANALYSIS_CHUNK_SPAN_DEG):
        try:
            start_result = await asyncio.wait_for(
                _period_composite(start_scenes, chunk_bbox),
                timeout=_COMPOSITE_TIMEOUT_SECONDS,
            )
            end_result = await asyncio.wait_for(
                _period_composite(end_scenes, chunk_bbox),
                timeout=_COMPOSITE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            failed_chunks.append(chunk_bbox)
            continue
        start_ndvi, start_grid, start_ok_chunk, start_bad = start_result
        end_ndvi, end_grid, end_ok_chunk, end_bad = end_result
        for item in [*start_ok_chunk, *end_ok_chunk]:
            accepted_by_key[
                f"{item.get('sceneId')}|{item.get('role')}"
            ] = item
        for item in [*start_bad, *end_bad]:
            rejected_by_key[
                f"{item.get('sceneId')}|{item.get('role')}"
            ] = item
        if (
            start_ndvi is None
            or end_ndvi is None
            or start_grid is None
            or end_grid is None
            or start_grid["crs"] is None
            or end_grid["crs"] is None
            or np.isnan(start_ndvi).all()
            or np.isnan(end_ndvi).all()
        ):
            failed_chunks.append(chunk_bbox)
            continue
        grid_check = _grid_differences(start_grid["info"], end_grid["info"])
        grid_alignment = "none"
        if not grid_check["compatibleWithoutResampling"]:
            end_ndvi = await asyncio.to_thread(
                reproject_to_grid,
                end_ndvi,
                src_transform=end_grid["transform"],
                src_crs=end_grid["crs"],
                dst_transform=start_grid["transform"],
                dst_crs=start_grid["crs"],
                dst_shape=start_grid["shape"],
                resampling=Resampling.bilinear,
            )
            grid_alignment = "end_reprojected_to_start"
        rows, cols = start_grid["shape"]
        chunk_records.append(
            {
                "bbox": chunk_bbox,
                "start_ndvi": start_ndvi[:rows, :cols],
                "end_ndvi": end_ndvi[:rows, :cols],
                "start_grid": start_grid,
                "end_grid": end_grid,
                "grid_check": grid_check,
                "grid_alignment": grid_alignment,
                "rows": rows,
                "cols": cols,
            }
        )

    start_ok = [
        item
        for item in accepted_by_key.values()
        if item.get("role") == "start_period"
    ]
    end_ok = [
        item
        for item in accepted_by_key.values()
        if item.get("role") == "end_period"
    ]
    datasets = {
        "accepted": [*start_ok, *end_ok],
        "rejected": list(rejected_by_key.values()),
    }
    actual_dates = {
        "start": _actual_dates(start_ok),
        "end": _actual_dates(end_ok),
    }
    period_diagnostics: dict[str, Any] = {
        "bbox": bbox,
        "aoi": {
            "bbox": bbox,
            "source": aoi_source,
            "geometryType": (
                aoi_geometry.get("type")
                if isinstance(aoi_geometry, dict)
                else None
            ),
            "requestedName": territory_name,
            "parent": (aoi.get("requestedTerritory") or {}).get("parent"),
            "displayName": territory_display,
        },
        "territorySelection": territory_info,
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
        "chunks": {
            "processed": len(chunk_records),
            "failed": len(failed_chunks),
            "spanDeg": ANALYSIS_CHUNK_SPAN_DEG,
        },
    }
    if not chunk_records:
        return _fail(
            context,
            "Не удалось прочитать сцены Sentinel-2 ни на одном участке территории",
            datasets,
            period_diagnostics,
            stage="scene_read",
        )

    first = chunk_records[0]
    start_grid = first["start_grid"]
    end_grid = first["end_grid"]
    start_transform = start_grid["transform"]
    start_crs = start_grid["crs"]
    grid_check = first["grid_check"]
    grid_alignment = first["grid_alignment"]

    processed_aoi_pixels = 0
    shared_pixels = 0
    valid_start_pixels = 0
    valid_end_pixels = 0
    zones: list[dict[str, Any]] = []
    for record in chunk_records:
        chunk_rows = int(record["rows"])
        chunk_cols = int(record["cols"])
        chunk_transform = record["start_grid"]["transform"]
        chunk_crs = record["start_grid"]["crs"]
        chunk_aoi = (
            np.ones((chunk_rows, chunk_cols), dtype=bool)
            if "bbox_polygon" in aoi_source
            else _aoi_mask(
                aoi_geometry,
                chunk_transform,
                (chunk_rows, chunk_cols),
                chunk_crs,
            )
        )
        chunk_valid_start = ~np.isnan(record["start_ndvi"]) & chunk_aoi
        chunk_valid_end = ~np.isnan(record["end_ndvi"]) & chunk_aoi
        chunk_shared = chunk_valid_start & chunk_valid_end
        processed_aoi_pixels += int(chunk_aoi.sum())
        valid_start_pixels += int(chunk_valid_start.sum())
        valid_end_pixels += int(chunk_valid_end.sum())
        shared_pixels += int(chunk_shared.sum())
        if not chunk_shared.any():
            continue
        zones.extend(
            build_change_zones(
                dict(
                    zip(
                        ("retained", "lost", "new"),
                        detect_changes(
                            classify_vegetation(
                                record["start_ndvi"], threshold
                            ),
                            classify_vegetation(
                                record["end_ndvi"], threshold
                            ),
                            valid_mask=chunk_shared,
                        ),
                        strict=False,
                    )
                ),
                chunk_transform,
                chunk_crs,
                territory_name,
                min_area_m2,
            )
        )
    if failed_chunks:
        # Часть участков не обработана: считаем полную площадь AOI по общей
        # сетке, чтобы не занизить unknown и не завысить покрытие.
        res_x = abs(float(start_transform.a))
        res_y = abs(float(start_transform.e))
        left, bottom, right, top = transform_bounds(
            "EPSG:4326", start_crs, *bbox, densify_pts=21
        )
        full_cols = max(1, math.ceil((right - left) / res_x))
        full_rows = max(1, math.ceil((top - bottom) / res_y))
        full_transform = Affine(res_x, 0, left, 0, -res_y, top)
        full_aoi = (
            np.ones((full_rows, full_cols), dtype=bool)
            if "bbox_polygon" in aoi_source
            else _aoi_mask(
                aoi_geometry,
                full_transform,
                (full_rows, full_cols),
                start_crs,
            )
        )
        total_pixels = int(full_aoi.sum())
        bbox_pixels = int(full_aoi.size)
    else:
        total_pixels = processed_aoi_pixels
        bbox_pixels = sum(
            int(record["rows"]) * int(record["cols"])
            for record in chunk_records
        )
    unknown_pixels = total_pixels - shared_pixels
    unprocessed_aoi_pixels = max(0, total_pixels - processed_aoi_pixels)
    shared_share = shared_pixels / total_pixels if total_pixels else 0.0
    unknown_share = unknown_pixels / total_pixels if total_pixels else 1.0
    center_lat = (float(bbox[1]) + float(bbox[3])) / 2
    pixel_area_m2 = grid_pixel_area_m2(start_transform, start_crs, center_lat)
    territory_area_km2 = (
        total_pixels * pixel_area_m2 / 1_000_000.0
        if pixel_area_m2 is not None
        else None
    )
    bbox_area_km2 = (
        bbox_pixels * pixel_area_m2 / 1_000_000.0
        if pixel_area_m2 is not None
        else None
    )
    coverage = {
        "validStart": (
            valid_start_pixels / total_pixels if total_pixels else 0.0
        ),
        "validEnd": valid_end_pixels / total_pixels if total_pixels else 0.0,
        "sharedValid": shared_share,
        "unknown": unknown_share,
        "totalPixels": total_pixels,
        "bboxPixels": bbox_pixels,
        "outsideAoiPixels": bbox_pixels - total_pixels,
        "processedAoiPixels": processed_aoi_pixels,
        "unprocessedAoiPixels": unprocessed_aoi_pixels,
        "sharedValidPixels": shared_pixels,
        "unknownPixels": unknown_pixels,
        "processedChunks": len(chunk_records),
        "totalChunks": len(chunk_records) + len(failed_chunks),
        "pixelAreaM2": pixel_area_m2,
        "territoryAreaKm2": territory_area_km2,
        "bboxAreaKm2": bbox_area_km2,
        "analysisAreaKm2": territory_area_km2,
        "processedAreaKm2": (
            processed_aoi_pixels * pixel_area_m2 / 1_000_000.0
            if pixel_area_m2 is not None
            else None
        ),
        "unprocessedAreaKm2": (
            unprocessed_aoi_pixels * pixel_area_m2 / 1_000_000.0
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
            "Доли покрытия считаются относительно административной границы "
            "территории; необработанные участки входят в unknown. "
            "Площади зон считает PostGIS после фильтрации min_area."
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
    if "bbox_polygon" in aoi_source:
        limitations.append(
            "Административная граница территории не получена: использован "
            "прямоугольник bbox, результат может включать пригороды."
        )
    limitations.append(
        "Вне границы запрошенной территории изменения не оценивались."
    )
    if any(
        item.get("windowDays") == EXPANDED_WINDOW_DAYS
        for item in [*start_ok, *end_ok]
    ):
        limitations.append(
            "Для покрытия окно поиска сцен расширено до ±"
            f"{EXPANDED_WINDOW_DAYS} дней внутри того же сезона; "
            "фактические даты указаны в результате."
        )
    if failed_chunks:
        limitations.append(
            f"Не обработано участков: {len(failed_chunks)} из "
            f"{len(chunk_records) + len(failed_chunks)}; их площадь учтена "
            "как unknown, изменения там не оценивались."
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
        "aoi": {
            "bbox": bbox,
            "source": aoi_source,
            "geometryType": (
                aoi_geometry.get("type")
                if isinstance(aoi_geometry, dict)
                else None
            ),
            "areaKm2": territory_area_km2,
            "bboxAreaKm2": bbox_area_km2,
            "requestedName": territory_name,
            "parent": (aoi.get("requestedTerritory") or {}).get("parent"),
            "displayName": territory_display,
        },
        "territorySelection": territory_info,
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
        "chunks": {
            "processed": len(chunk_records),
            "failed": len(failed_chunks),
            "spanDeg": ANALYSIS_CHUNK_SPAN_DEG,
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

    # Зоны уже собраны по участкам (с AOI-маской).
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
                "aoi_geometry": aoi_geometry,
                "aoi_source": aoi_source,
                "aoi_area_km2": territory_area_km2,
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
            "aoi_geometry": aoi_geometry,
            "aoi_source": aoi_source,
            "aoi_area_km2": territory_area_km2,
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
