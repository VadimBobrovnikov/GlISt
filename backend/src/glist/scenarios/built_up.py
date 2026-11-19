"""Сценарий «Изменение застройки» (Sentinel-2, спектральный прокси).

Что считается застройкой
------------------------
Спектральный прокси застроенных/непроницаемых поверхностей по NDBI:

    NDBI = (B11 − B08) / (B11 + B08)

где B11 — SWIR1 (20 м), B08 — NIR (10 м). Пиксель относится к классу, если
NDBI > порога И NDVI ниже порога растительности (иначе крыши с газонами и
кроны деревьев попадали бы в застройку). Отдельные здания 20-метровым
снимком не распознаются; открытый грунт спектрально близок к застройке и
может попадать в класс — это указано в ограничениях результата.

Данные: Sentinel-2 L2A (Earth Search, COG), маска SCL (облака/тени/снег).
Классы: застройка сохранилась / появилась / исчезла + unknown (нет валидных
данных на одной из дат).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import numpy as np

from glist.agents.analyst import (
    MAX_CLOUD_COVER,
    _actual_dates,
    _aoi_mask,
    _band_scaling,
    _grid_public,
    _parse_date,
    _read_band_window,
    _resolve_territory_aoi,
    _scene_meta,
    _window,
)
from glist.agents.researcher import researcher_node
from glist.scenarios.grid import affine_of, target_grid, warp_mask_to_grid, warp_to_grid
from glist.tools.change_zones import build_change_zones
from glist.tools.stac_search import search_sentinel2_scenes

logger = logging.getLogger("glist.scenarios.built_up")

ANALYSIS_TYPE = "built_up_change"

REFLECTANCE_SCALE = 0.0001
SCL_SCALE = 1.0
# Валидные классы SCL: растительность, не вегетация, вода, без снега.
_VALID_SCL = (4, 5, 6, 7, 11)
# Порог NDBI: выше — поверхность спектрально похожа на застроенную.
BUILT_UP_THRESHOLD = 0.05
# Пиксели с NDVI выше порога — растительность, к застройке не относятся.
VEGETATION_MAX_NDVI = 0.30
# Минимальная доля общего валидного покрытия AOI для интерпретации.
MIN_SHARED_COVERAGE = 0.5
MAX_SCENES_PER_PERIOD = 6
MIN_AREA_M2 = 1000.0
# Единая сетка анализа (метры): 20 м сцены разных тайлов сводятся к ней.
GRID_RESOLUTION_M = 30.0
_READ_CONCURRENCY = 4

METHOD_NAME = "NDBI (Sentinel-2 L2A), медианный композит"
METHOD_FORMULA = "(B11 − B08) / (B11 + B08) > 0.05 при NDVI < 0.30"


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _fail(
    context: dict[str, Any],
    message: str,
    *,
    category: str = "analysis_error",
    stage: str = "built_up",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Возвращает отказ сценария (без зон и без подмены сценария)."""
    context["change_zones"] = []
    payload = dict(diagnostics or {})
    payload.setdefault("analysisType", ANALYSIS_TYPE)
    payload.setdefault("technicalSuccess", category != "analysis_error")
    payload.setdefault("dataQualityAssessed", True)
    payload["failure"] = {
        "category": category,
        "stage": stage,
        "message": message,
        "technicalSuccess": category != "analysis_error",
        "dataQualityAssessed": True,
    }
    payload.setdefault("limitations", [message])
    payload.setdefault(
        "interpretability",
        {"status": "not_evaluated", "reasons": [message]},
    )
    context["analysis_diagnostics"] = payload
    context["analysis_failure"] = payload["failure"]
    prefix = (
        "Технический сбой" if category == "analysis_error" else "Результат не прошёл проверку"
    )
    return {
        "context": context,
        "analysis_result": {
            "mode": "single",
            "summary_text": f"{prefix}: {message}",
            "explanation_text": message,
        },
        "visualization": {"summary_text": message},
        "validation": {
            "outcome": "failed",
            "category": category,
            "reasons": [message],
        },
        "messages": [message],
        "errors": [message],
    }


async def _search_period_scenes(
    bbox: list[float],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Ищет сцены периода; при пустом результате пробует более облачные."""
    scenes = await search_sentinel2_scenes(
        bbox,
        start,
        end,
        max_cloud_cover=MAX_CLOUD_COVER,
        limit=MAX_SCENES_PER_PERIOD * 3,
    )
    if scenes:
        return scenes[:MAX_SCENES_PER_PERIOD]
    # Облачный сезон: ослабляем порог, SCL всё равно маскирует облака.
    scenes = await search_sentinel2_scenes(
        bbox,
        start,
        end,
        max_cloud_cover=60,
        limit=MAX_SCENES_PER_PERIOD * 3,
    )
    return scenes[:MAX_SCENES_PER_PERIOD]


async def _read_scene_index(
    scene: dict[str, Any],
    bbox: list[float],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Читает NDBI/NDVI/валидную маску сцены на сетке SWIR (20 м)."""
    swir_url = scene.get("swir16_url")
    nir_url = scene.get("nir_url")
    red_url = scene.get("red_url")
    scl_url = scene.get("scl_url")
    if not (swir_url and nir_url and red_url and scl_url):
        return None
    swir_scale, swir_offset, _ = _band_scaling(scene, "swir16", REFLECTANCE_SCALE)
    nir_scale, nir_offset, _ = _band_scaling(scene, "nir", REFLECTANCE_SCALE)
    red_scale, red_offset, _ = _band_scaling(scene, "red", REFLECTANCE_SCALE)
    scl_scale, scl_offset, _ = _band_scaling(scene, "scl", SCL_SCALE)
    bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
    crs = (
        f"EPSG:{scene.get('proj_epsg')}"
        if scene.get("proj_epsg") is not None
        else None
    )
    async with semaphore:
        try:
            swir, transform = await asyncio.to_thread(
                _read_band_window, swir_url, bbox_tuple, swir_scale, None, swir_offset
            )
            if swir.size == 0:
                return None
            shape = swir.shape
            nir, _ = await asyncio.to_thread(
                _read_band_window, nir_url, bbox_tuple, nir_scale, shape, nir_offset
            )
            red, _ = await asyncio.to_thread(
                _read_band_window, red_url, bbox_tuple, red_scale, shape, red_offset
            )
            scl, _ = await asyncio.to_thread(
                _read_band_window, scl_url, bbox_tuple, scl_scale, shape, scl_offset
            )
        except Exception:  # noqa: BLE001 — одна сцена не должна ронять сценарий
            logger.exception("built_up: ошибка чтения сцены %s", scene.get("id"))
            return None

    scl_int = np.rint(scl).astype("int16")
    scl_ok = np.isin(scl_int, _VALID_SCL)
    # Метаданные offset не всегда соответствуют данным (встречаются сцены,
    # где DN не сдвинут на BOA_ADD_OFFSET, а offset объявлен). Проверяем по
    # красному каналу: если после коррекции у большинства валидных пикселей
    # отражательная способность отрицательна — коррекция не применяется.
    offset_ignored = False
    if (swir_offset or nir_offset or red_offset) and bool(scl_ok.any()):
        negative_share = float((red[scl_ok] < 0).mean()) if red.size else 0.0
        if negative_share > 0.3:
            logger.warning(
                "built_up: метаданные offset не соответствуют данным сцены %s "
                "(отрицательных пикселей red %.0f%%), коррекция отключена",
                scene.get("id"),
                negative_share * 100,
            )
            swir = swir - swir_offset
            nir = nir - nir_offset
            red = red - red_offset
            offset_ignored = True
    valid = scl_ok & (swir > 0) & (nir > 0) & (red > 0)
    ndbi = np.full(shape, np.nan, dtype="float32")
    ndvi = np.full(shape, np.nan, dtype="float32")
    swir_f = swir.astype("float32")
    nir_f = nir.astype("float32")
    red_f = red.astype("float32")
    swir_nir = swir_f + nir_f
    nir_red = nir_f + red_f
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(swir_f - nir_f, swir_nir, out=ndbi, where=valid & (swir_nir > 0))
        np.divide(nir_f - red_f, nir_red, out=ndvi, where=valid & (nir_red > 0))
    return {
        "ndbi": ndbi,
        "ndvi": ndvi,
        "valid": valid,
        "grid": _grid_public(transform, crs, shape),
        "transform": transform,
        "crs": crs,
        "scene": scene,
        "offsetIgnored": offset_ignored,
    }


def _composite(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Медианный композит NDBI/NDVI по валидным наблюдениям одной сетки."""
    shape = entries[0]["ndbi"].shape
    ndbi_stack = np.stack([entry["ndbi"] for entry in entries])
    ndvi_stack = np.stack([entry["ndvi"] for entry in entries])
    valid_stack = np.stack([entry["valid"] for entry in entries])
    valid = valid_stack.any(axis=0)
    with np.errstate(all="ignore"):
        ndbi = np.nanmedian(ndbi_stack, axis=0)
        ndvi = np.nanmedian(ndvi_stack, axis=0)
    ndbi = np.where(valid, ndbi, np.nan)
    ndvi = np.where(valid, ndvi, np.nan)
    return {
        "ndbi": ndbi,
        "ndvi": ndvi,
        "valid": valid,
        "transform": entries[0]["transform"],
        "crs": entries[0]["crs"],
        "grid": entries[0]["grid"],
        "shape": shape,
        "scenes": [entry["scene"] for entry in entries],
    }



def classify_change(
    start_ndbi: np.ndarray,
    start_ndvi: np.ndarray,
    end_ndbi: np.ndarray,
    end_ndvi: np.ndarray,
    valid: np.ndarray,
    aoi: np.ndarray,
) -> dict[str, np.ndarray]:
    """Классификация застройки и её изменений на общей валидной сетке.

    Чистая функция без сети: используется обработчиком и проверяется на
    фиксированных массивах. Пиксель считается застроенным, если NDBI выше
    порога И NDVI ниже порога растительности. Невалидные пиксели (облака,
    тени, снег, NoData) не попадают ни в один класс — это «unknown», а не
    «застройки нет».
    """
    shared = np.asarray(valid, dtype=bool) & np.asarray(aoi, dtype=bool)
    with np.errstate(invalid="ignore"):
        built_start = (
            (start_ndbi > BUILT_UP_THRESHOLD)
            & (start_ndvi < VEGETATION_MAX_NDVI)
            & shared
        )
        built_end = (
            (end_ndbi > BUILT_UP_THRESHOLD)
            & (end_ndvi < VEGETATION_MAX_NDVI)
            & shared
        )
    return {
        "retained": built_start & built_end,
        "new": (~built_start) & built_end & shared,
        "lost": built_start & (~built_end) & shared,
        "built_start": built_start,
        "built_end": built_end,
        "unknown": ~shared,
    }


PERIOD_HALF_DAYS = 45


def _comparable_centers(
    start: date,
    end: date,
) -> tuple[date, date, dict[str, Any]]:
    """Подбирает дату «до» в том же сезоне, что и «после», внутри периода.

    Сравнение зимы (снег маскируется) с осенью даёт низкое покрытие и
    смещённые спектральные индексы. Дата «до» сдвигается на тот же месяц и
    день, что у «после», но не выходит за пределы запрошенного периода;
    сдвиг явно фиксируется в диагностике.
    """
    import calendar

    month, day = end.month, end.day
    before: date | None = None
    # Берём самую раннюю дату в том же сезоне: сравнение должно охватывать
    # весь запрошенный период, а не последний год.
    for year in range(start.year, end.year):
        day_clamped = min(day, calendar.monthrange(year, month)[1])
        candidate = date(year, month, day_clamped)
        if candidate < start:
            continue
        if (end - candidate).days < 180:
            break
        before = candidate
        break
    if before is None:
        before = start
    return (
        before,
        end,
        {
            "requestedCenters": {
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "usedCenters": {
                "start": before.isoformat(),
                "end": end.isoformat(),
            },
            "seasonAdjusted": before != start,
            "method": "same_season" if before != start else "requested",
        },
    )


async def _read_period(
    scenes: list[dict[str, Any]],
    bbox: list[float],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Читает сцены периода и приводит все тайлы к общей сетке анализа."""
    semaphore = asyncio.Semaphore(_READ_CONCURRENCY)
    results = await asyncio.gather(
        *(_read_scene_index(scene, bbox, semaphore) for scene in scenes)
    )
    entries: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for scene, result in zip(scenes, results, strict=False):
        if result is None:
            rejected.append({**_scene_meta(scene), "reason": "read_error"})
            continue
        try:
            entries.append(
                {
                    "ndbi": warp_to_grid(result["ndbi"], result["grid"], target),
                    "ndvi": warp_to_grid(result["ndvi"], result["grid"], target),
                    "valid": warp_mask_to_grid(result["valid"], result["grid"], target),
                    "scene": result["scene"],
                    "grid": target,
                    "transform": affine_of(target),
                    "crs": target["crs"],
                    "sourceGrid": result["grid"],
                    "offsetIgnored": bool(result.get("offsetIgnored")),
                }
            )
        except Exception:  # noqa: BLE001 — сбой перепроецирования одной сцены
            logger.exception(
                "built_up: не удалось привести сцену %s к сетке анализа",
                scene.get("id"),
            )
            rejected.append({**_scene_meta(scene), "reason": "warp_error"})
    return {"entries": entries, "rejected": rejected}


def _accepted_meta(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Метаданные принятых сцен в форме, ожидаемой API источников."""
    accepted: list[dict[str, Any]] = []
    for scene in scenes:
        meta = _scene_meta(scene)
        meta["acquiredAt"] = meta.get("datetime")
        meta["resolutionM"] = 20
        accepted.append(meta)
    return accepted


async def _prepare_inputs(
    state: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Территория и период: подтверждённая AOI либо Researcher (без подмены)."""
    resolved = _as_dict(context.get("resolved_territory"))
    geometry = resolved.get("geometry")
    raw_bbox = resolved.get("bbox")
    name = str(resolved.get("displayName") or resolved.get("name") or "").strip()
    period = _as_dict(context.get("period"))

    if not (isinstance(geometry, dict) and isinstance(raw_bbox, list) and len(raw_bbox) == 4):
        outcome = await researcher_node({**state, "context": context})
        context = {**context, **_as_dict(outcome.get("context"))}
        territories = [
            item
            for item in _as_list(context.get("territories"))
            if isinstance(item, dict)
        ]
        if not territories:
            return {
                "error": (
                    "Не удалось определить территорию запроса. Укажите город или "
                    "район, либо выделите участок на карте."
                ),
                "stage": "input_validation",
                "context": context,
            }
        territory = territories[0]
        name = str(territory.get("name") or "").strip() or name
        aoi = await _resolve_territory_aoi(territory, name)
        if aoi.get("error"):
            return {
                "error": str(aoi["error"]),
                "stage": str(aoi.get("stage") or "territory_selection"),
                "context": context,
            }
        geometry = aoi.get("geometry")
        raw_bbox = aoi.get("bbox")
        name = str(aoi.get("displayName") or name)
        period = _as_dict(context.get("period"))
    elif not (_parse_date(period.get("start")) and _parse_date(period.get("end"))):
        # Территория подтверждена пользователем, но период задаёт Researcher:
        # повторного геокодинга нет, AOI берётся из запроса.
        outcome = await researcher_node({**state, "context": context})
        context = {**context, **_as_dict(outcome.get("context"))}
        period = _as_dict(context.get("period"))

    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        return {
            "error": "Не удалось получить границы территории.",
            "stage": "territory_selection",
            "context": context,
        }
    bbox = [float(value) for value in raw_bbox]
    start = _parse_date(period.get("start"))
    end = _parse_date(period.get("end"))
    if start is None or end is None:
        return {
            "error": "Не удалось определить период анализа. Укажите годы или даты.",
            "stage": "input_validation",
            "context": context,
        }
    if start > end:
        start, end = end, start
    west, south, east, north = bbox
    if east - west > 0.6 or north - south > 0.6:
        return {
            "error": (
                "Территория слишком большая для анализа застройки (20 м): "
                f"{east - west:.2f}°×{north - south:.2f}°. Выберите участок."
            ),
            "stage": "aoi_size",
            "context": context,
        }
    return {
        "error": None,
        "context": context,
        "name": name or "Территория",
        "geometry": geometry,
        "bbox": bbox,
        "period": (start, end),
    }


async def run_built_up_change(state: dict[str, Any]) -> dict[str, Any]:
    """Полный расчёт сценария «изменение застройки» (NDBI-прокси)."""
    context = _as_dict(state.get("context"))
    prepared = await _prepare_inputs(state, context)
    context = _as_dict(prepared.get("context")) or context
    if prepared.get("error"):
        return _fail(
            context,
            str(prepared["error"]),
            stage=str(prepared.get("stage") or "built_up"),
        )

    name = str(prepared["name"])
    bbox = [float(value) for value in prepared["bbox"]]
    raw_geometry = prepared.get("geometry")
    geometry = raw_geometry if isinstance(raw_geometry, dict) else None
    period = prepared["period"]
    start, end = period
    before_center, after_center, period_selection = _comparable_centers(start, end)

    start_from, start_to = _window(before_center, PERIOD_HALF_DAYS)
    end_from, end_to = _window(after_center, PERIOD_HALF_DAYS)
    today = date.today().isoformat()
    end_to = min(end_to, today)
    start_to = min(start_to, today)
    start_scenes = await _search_period_scenes(bbox, start_from, start_to)
    end_scenes = await _search_period_scenes(bbox, end_from, end_to)
    # Общая сетка для обеих дат: тайлы разных MGRS приводятся к одной сетке.
    target = target_grid(bbox, GRID_RESOLUTION_M)

    diagnostics: dict[str, Any] = {
        "analysisType": ANALYSIS_TYPE,
        "method": {
            "name": METHOD_NAME,
            "formula": METHOD_FORMULA,
            "resolutionM": GRID_RESOLUTION_M,
            "sensor": "Sentinel-2 MSI (L2A)",
        },
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "startWindow": {"from": start_from, "to": start_to},
            "endWindow": {"from": end_from, "to": end_to},
        },
        "periodSelection": period_selection,
        "dates": {
            "start": _actual_dates(start_scenes),
            "end": _actual_dates(end_scenes),
        },
        "scenes": {
            "start": _accepted_meta(start_scenes),
            "end": _accepted_meta(end_scenes),
            "rejected": [],
        },
        "grid": {
            "crs": target["crs"],
            "shape": target["shape"],
            "resolution": target["resolution"],
            "note": (
                "все сцены (включая разные MGRS-тайлы) перепроецированы на "
                "единую сетку анализа"
            ),
        },
    }
    if not start_scenes or not end_scenes:
        missing = "начале периода" if not start_scenes else "конце периода"
        return _fail(
            context,
            f"Нет доступных сцен Sentinel-2 в {missing} "
            f"({start.isoformat()}…{end.isoformat()}). Изменения не оценивались.",
            category="data_quality",
            stage="stac_search",
            diagnostics=diagnostics,
        )

    start_read = await _read_period(start_scenes, bbox, target)
    end_read = await _read_period(end_scenes, bbox, target)
    diagnostics["scenes"]["rejected"] = [
        *start_read["rejected"],
        *end_read["rejected"],
    ]
    offset_ignored_scenes = [
        str(entry["scene"].get("id"))
        for entry in [*start_read["entries"], *end_read["entries"]]
        if entry.get("offsetIgnored")
    ]
    diagnostics["scaling"] = {
        "offsetIgnoredScenes": offset_ignored_scenes,
        "note": (
            "у перечисленных сцен объявленный offset не соответствовал данным "
            "(отражательная способность уходила в минус) — коррекция отключена"
            if offset_ignored_scenes
            else "offset применён согласно метаданным STAC"
        ),
    }
    if not start_read["entries"] or not end_read["entries"]:
        failed_side = "начало" if not start_read["entries"] else "конец"
        all_read_errors = all(
            str(item.get("reason")) == "read_error"
            for item in [
                *start_read["rejected"],
                *end_read["rejected"],
            ]
        )
        category = "analysis_error" if all_read_errors else "data_quality"
        return _fail(
            context,
            f"Не удалось получить пригодные наблюдения на {failed_side} периода "
            f"(сцен: {len(start_scenes)} / {len(end_scenes)}).",
            category=category,
            stage="scene_read" if all_read_errors else "coverage",
            diagnostics=diagnostics,
        )

    start_composite = _composite(start_read["entries"])
    end_composite = _composite(end_read["entries"])

    shape = start_composite["shape"]
    transform = start_composite["transform"]
    crs = start_composite["crs"]

    aoi = _aoi_mask(geometry, transform, shape, crs)
    start_valid = np.asarray(start_composite["valid"], dtype=bool)
    end_valid = np.asarray(end_composite["valid"], dtype=bool)
    shared = start_valid & end_valid & aoi
    aoi_pixels = int(aoi.sum())
    shared_pixels = int(shared.sum())
    coverage_ratio = (shared_pixels / aoi_pixels) if aoi_pixels else 0.0
    pixel_area_m2 = float(target.get("pixelAreaM2") or 0.0)
    analysis_area_km2 = aoi_pixels * pixel_area_m2 / 1_000_000.0
    shared_area_km2 = shared_pixels * pixel_area_m2 / 1_000_000.0
    unknown_area_km2 = max(0.0, analysis_area_km2 - shared_area_km2)
    coverage = {
        "aoiPixels": aoi_pixels,
        "sharedValidPixels": shared_pixels,
        "sharedValid": round(coverage_ratio, 6),
        "unknown": round(1.0 - coverage_ratio, 6) if aoi_pixels else 1.0,
        "analysisAreaKm2": round(analysis_area_km2, 6),
        "sharedValidAreaKm2": round(shared_area_km2, 6),
        "unknownAreaKm2": round(unknown_area_km2, 6),
        "minCoverageThreshold": MIN_SHARED_COVERAGE,
        "note": (
            f"покрытие по пикселям сетки {GRID_RESOLUTION_M:.0f} м "
            "внутри границы территории"
        ),
    }
    diagnostics["coverage"] = coverage
    diagnostics["classification"] = {
        "method": METHOD_NAME,
        "formula": METHOD_FORMULA,
        "ndbiThreshold": BUILT_UP_THRESHOLD,
        "ndviMax": VEGETATION_MAX_NDVI,
        "minAreaM2": MIN_AREA_M2,
        "validSclClasses": list(_VALID_SCL),
    }

    limitations = [
        "Класс — спектральный прокси застроенных/непроницаемых поверхностей "
        "(NDBI, 20 м), а не распознавание отдельных зданий.",
        "Открытый грунт, стройплощадки и часть покрытий спектрально близки к "
        "застройке и могут попадать в класс.",
        "Пиксели с NDVI ≥ 0.30 не относятся к застройке даже при высоком NDBI.",
        "Вне общего валидного покрытия (облака/тени/снег/NoData) изменения не "
        "оценивались и не считаются отсутствием застройки.",
    ]
    if offset_ignored_scenes:
        limitations.append(
            "У части сцен объявленный offset не соответствовал данным: для них "
            "коррекция offset отключена (перечислены в диагностике scaling). "
            "Это влияет на абсолютные значения индексов."
        )
    months_start = {value[5:7] for value in diagnostics["dates"]["start"] if isinstance(value, str)}
    months_end = {value[5:7] for value in diagnostics["dates"]["end"] if isinstance(value, str)}
    if period_selection.get("seasonAdjusted"):
        limitations.append(
            "Дата «до» сдвинута к тому же сезону, что и «после»: "
            f"{period_selection['usedCenters']['start']} вместо "
            f"{period_selection['requestedCenters']['start']} (внутри "
            "запрошенного периода, без выхода за его границы)."
        )
    if months_start and months_end and months_start != months_end:
        limitations.append(
            "Даты периодов приходятся на разные месяцы "
            f"({sorted(months_start)} → {sorted(months_end)}): сезонность влияет "
            "на спектральные индексы."
        )
    diagnostics["limitations"] = limitations
    diagnostics["technicalSuccess"] = True
    diagnostics["dataQualityAssessed"] = True

    if coverage_ratio < MIN_SHARED_COVERAGE:
        reason = (
            f"Общее валидное покрытие {coverage_ratio:.1%} ниже порога "
            f"{MIN_SHARED_COVERAGE:.0%}: результат не интерпретируется."
        )
        diagnostics["interpretability"] = {
            "status": "insufficient",
            "minCoverageThreshold": MIN_SHARED_COVERAGE,
            "reasons": [reason],
        }
        diagnostics["limitations"] = [*limitations, reason]
        context["analysis_diagnostics"] = diagnostics
        context["datasets"] = {
            "accepted": _accepted_meta([*start_scenes, *end_scenes]),
            "rejected": diagnostics["scenes"]["rejected"],
        }
        return _fail(
            context,
            reason,
            category="data_quality",
            stage="coverage",
            diagnostics=diagnostics,
        )

    classes = classify_change(
        start_composite["ndbi"],
        start_composite["ndvi"],
        end_composite["ndbi"],
        end_composite["ndvi"],
        shared,
        aoi,
    )
    zones = build_change_zones(
        {
            "retained": classes["retained"],
            "lost": classes["lost"],
            "new": classes["new"],
        },
        transform,
        crs,
        name,
        MIN_AREA_M2,
    )
    counts = {
        change_class: sum(1 for zone in zones if zone["change_class"] == change_class)
        for change_class in ("retained", "lost", "new")
    }

    summary = (
        "Застройка (спектральный прокси NDBI, Sentinel-2 20 м): "
        f"появилось зон — {counts['new']}, исчезло — {counts['lost']}, "
        f"сохранилось — {counts['retained']}. "
        f"Общее валидное покрытие территории: {coverage_ratio:.1%}."
    )
    explanation = (
        "Сравнивались медианные композиты NDBI и NDVI за два периода. "
        f"Период «до»: {', '.join(diagnostics['dates']['start']) or '—'}; "
        f"период «после»: {', '.join(diagnostics['dates']['end']) or '—'}. "
        "Класс описывает застроенные/непроницаемые поверхности на сетке 20 м; "
        "отдельные здания и этажность по таким данным не определяются. "
        "Площади зон рассчитаны PostGIS по итоговым полигонам."
    )
    diagnostics["interpretability"] = {
        "status": "sufficient",
        "minCoverageThreshold": MIN_SHARED_COVERAGE,
        "reasons": [],
    }
    diagnostics["zonesCount"] = counts
    context["analysis_diagnostics"] = diagnostics
    context["analysis_failure"] = None
    context["territories"] = [{"name": name}]
    context["change_zones"] = zones
    context["aoi_geometry"] = geometry
    context["period"] = {"start": start.isoformat(), "end": end.isoformat()}
    context["datasets"] = {
        "accepted": _accepted_meta([*start_scenes, *end_scenes]),
        "rejected": diagnostics["scenes"]["rejected"],
    }
    context["summary_text"] = summary
    context["text_explanation"] = explanation

    return {
        "context": context,
        "analysis_result": {
            "mode": "single",
            "summary_text": summary,
            "explanation_text": explanation,
            "sources_summary": {"datasets": context["datasets"]},
            "citations": [],
        },
        "visualization": {
            "summary_text": summary,
            "explanation_text": explanation,
        },
        "validation": {
            "outcome": "passed",
            "category": "none",
            "reasons": [],
        },
        "messages": [summary],
        "errors": [],
    }
