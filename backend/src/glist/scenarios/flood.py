"""Сценарий «Зоны затопления» (Sentinel-2, MNDWI на дату события).

Почему оптика, а не радар
-------------------------
В доступном каталоге (Earth Search) Sentinel-1 представлен только
``sentinel-1-grd`` — амплитудные COG без калибровочной таблицы и без
радиометрической/террейн-коррекции. Пороги обратного рассеяния по таким
данным недостоверны, продуктов RTC/водных масок в инфраструктуре нет,
поэтому радар не используется (осознанный отказ, а не «перекрашивание»
классов растительности).

Метод
-----
MNDWI = (B03 − B11) / (B03 + B11); вода там, где MNDWI > 0 и пиксель валиден
по SCL (облака/тени/снег исключаются). Сравниваются две короткие выдержки:

* **дата события** — сцены в окне ±5 дней вокруг указанной пользователем даты
  (широкий сезонный композит здесь недопустим: он может скрыть короткое
  затопление);
* **базовый период** — короткая выдержка до события. Окна проверяются по
  порядку (15–45, 45–75, 75–105, 105–150, 150–210, 210–300 дней до события),
  и берётся **ближайшее к событию окно без снега/льда**: под снегом «постоянную
  воду» отделить нельзя, а любая вода на дату события выглядела бы паводком.
  Если бесснежного окна нет, расчёт не выдаётся за достоверный.

Снег/лёд (SCL 11) исключён из валидных наблюдений: такие пиксели попадают в
«неизвестно», а не считаются «воды нет». Это не даёт занизить постоянную воду
в заснеженной базе.

Постоянная вода — вода в базовом периоде; временное затопление — вода на дату
события, которой не было в базовом периоде; «вода ушла» — вода была в базовом
периоде, на дату события её нет.

Ограничения: при сплошной облачности событие не наблюдается (результат
помечается как непригодный); глубина, ущерб и опасность не рассчитываются.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any

import numpy as np

from glist.agents.analyst import (
    _aoi_mask,
    _band_scaling,
    _grid_public,
    _read_band_window,
    _resolve_territory_aoi,
    _scene_meta,
)
from glist.agents.researcher import researcher_node
from glist.scenarios.grid import affine_of, target_grid, warp_mask_to_grid
from glist.tools.change_zones import build_change_zones
from glist.tools.stac_search import search_sentinel2_scenes

logger = logging.getLogger("glist.scenarios.flood")

ANALYSIS_TYPE = "flood_extent"

REFLECTANCE_SCALE = 0.0001
SCL_SCALE = 1.0
# SCL 4 vegetation, 5 bare, 6 water, 7 unclassified. Снег/лёд (11) — не
# наблюдение воды: такие пиксели уходят в «неизвестно», а не в «воды нет».
_VALID_SCL = (4, 5, 6, 7)
# Порог MNDWI: выше — открытая вода.
MNDWI_THRESHOLD = 0.0
# Вода поглощает ближний ИК: снег и грунт с высоким NIR в воду не попадают.
NIR_MAX_REFLECTANCE = 0.15
# Короткое окно вокруг даты события: сезонный композит скрыл бы паводок.
EVENT_WINDOW_DAYS = 5
# Базовый период: окна в днях до события, от ближнего к дальнему.
BASELINE_WINDOWS: tuple[tuple[int, int], ...] = (
    (15, 45),
    (45, 75),
    (75, 105),
    (105, 150),
    (150, 210),
    (210, 300),
)
BASELINE_SCENES_PER_WINDOW = 4
MAX_SCENES_PER_PERIOD = 8
# Начало архива Sentinel-2 L2A в используемом каталоге (Earth Search).
_S2_L2A_START = date(2017, 1, 1)
_SCL_SNOW = 11
_SCL_PROBE_SHAPE = (96, 96)
_BASELINE_PROBE_SCENES = 3
# Доля снега/льда в базовом периоде, выше которой база непригодна.
SNOW_MAX_SHARE = 0.2
BASELINE_MIN_VALID = 0.3
MIN_SHARED_COVERAGE = 0.4
# Покрытие, ниже которого результат помечается как ограниченный (не «точный»).
LIMITED_COVERAGE = 0.7
SNOW_WARN_SHARE = 0.05
GRID_RESOLUTION_M = 30.0
MIN_AREA_M2 = 1000.0
_READ_CONCURRENCY = 4

METHOD_NAME = "MNDWI (Sentinel-2 L2A) на дату события и в базовом периоде"
METHOD_FORMULA = "(B03 − B11) / (B03 + B11) > 0 и NIR < 0.15 при валидных SCL"

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_DATE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DATE_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")
_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(20\d{2})\b",
    re.IGNORECASE,
)


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_event_date(text: str) -> date | None:
    """Извлекает дату события из текста запроса (ISO, dd.mm.yyyy, «5 апреля 2026»)."""
    match = _DATE_ISO.search(text)
    if match is not None:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = _DATE_DOT.search(text)
    if match is not None:
        return _safe_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = _DATE_TEXT.search(text)
    if match is not None:
        month = _MONTHS.get(match.group(2).lower())
        if month is not None:
            return _safe_date(int(match.group(3)), month, int(match.group(1)))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fail(
    context: dict[str, Any],
    message: str,
    *,
    category: str = "analysis_error",
    stage: str = "flood",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Возвращает отказ сценария: без зон и без подмены анализа зелени."""
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
        "Технический сбой"
        if category == "analysis_error"
        else "Результат не прошёл проверку"
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


async def _read_scene_water(
    scene: dict[str, Any],
    bbox: list[float],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Читает MNDWI сцены и валидную маску на сетке SWIR (20 м)."""
    green_url = scene.get("green_url")
    swir_url = scene.get("swir16_url")
    nir_url = scene.get("nir_url")
    scl_url = scene.get("scl_url")
    if not (green_url and swir_url and nir_url and scl_url):
        return None
    green_scale, green_offset, _ = _band_scaling(scene, "green", REFLECTANCE_SCALE)
    swir_scale, swir_offset, _ = _band_scaling(scene, "swir16", REFLECTANCE_SCALE)
    nir_scale, nir_offset, _ = _band_scaling(scene, "nir", REFLECTANCE_SCALE)
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
            green, _ = await asyncio.to_thread(
                _read_band_window, green_url, bbox_tuple, green_scale, shape, green_offset
            )
            nir, _ = await asyncio.to_thread(
                _read_band_window, nir_url, bbox_tuple, nir_scale, shape, nir_offset
            )
            scl, _ = await asyncio.to_thread(
                _read_band_window, scl_url, bbox_tuple, scl_scale, shape, scl_offset
            )
        except Exception:  # noqa: BLE001 — одна сцена не должна ронять сценарий
            logger.exception("flood: ошибка чтения сцены %s", scene.get("id"))
            return None

    scl_int = np.rint(scl).astype("int16")
    scl_ok = np.isin(scl_int, _VALID_SCL)
    offset_ignored = False
    if (green_offset or swir_offset or nir_offset) and bool(scl_ok.any()):
        negative_share = float((green[scl_ok] < 0).mean()) if green.size else 0.0
        if negative_share > 0.3:
            logger.warning(
                "flood: offset не соответствует данным сцены %s (green<0 у %.0f%%), "
                "коррекция отключена",
                scene.get("id"),
                negative_share * 100,
            )
            green = green - green_offset
            swir = swir - swir_offset
            nir = nir - nir_offset
            offset_ignored = True
    valid = scl_ok & (green > 0) & (swir > 0) & (nir > 0)
    mndwi = np.full(shape, np.nan, dtype="float32")
    green_f = green.astype("float32")
    swir_f = swir.astype("float32")
    total = green_f + swir_f
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(green_f - swir_f, total, out=mndwi, where=valid & (total > 0))
    # Вода: MNDWI > 0 И низкий NIR (снег/грунт/крыши с высоким NIR исключаются).
    water = valid & (mndwi > MNDWI_THRESHOLD) & (nir < NIR_MAX_REFLECTANCE)
    snow_share = float((scl_int == 11).mean()) if scl_int.size else 0.0
    return {
        "mndwi": mndwi,
        "snowShare": snow_share,
        "valid": valid,
        "water": water,
        "grid": _grid_public(transform, crs, shape),
        "scene": scene,
        "offsetIgnored": offset_ignored,
    }


def combine_water(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Объединяет наблюдения: вода в ≥ половине валидных сцен периода.

    Короткое окно события (±5 дней) не «размывает» паводок сезонным
    композитом, но одиночный шумный пиксель не считается затоплением.
    """
    valid_stack = np.stack([entry["valid"] for entry in entries])
    water_stack = np.stack([entry["water"] for entry in entries])
    valid = valid_stack.any(axis=0)
    valid_count = valid_stack.sum(axis=0)
    water_count = water_stack.sum(axis=0)
    share = np.divide(
        water_count,
        np.maximum(valid_count, 1),
        dtype="float32",
    )
    water = valid & (share >= 0.5)
    return {"water": water, "valid": valid, "scenes": [entry["scene"] for entry in entries]}


async def _read_period(
    scenes: list[dict[str, Any]],
    bbox: list[float],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Читает сцены периода и приводит тайлы к общей сетке анализа."""
    semaphore = asyncio.Semaphore(_READ_CONCURRENCY)
    results = await asyncio.gather(
        *(_read_scene_water(scene, bbox, semaphore) for scene in scenes)
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
                    "water": warp_mask_to_grid(result["water"], result["grid"], target),
                    "valid": warp_mask_to_grid(result["valid"], result["grid"], target),
                    "scene": result["scene"],
                    "offsetIgnored": bool(result.get("offsetIgnored")),
                    "snowShare": float(result.get("snowShare") or 0.0),
                }
            )
        except Exception:  # noqa: BLE001 — сбой перепроецирования одной сцены
            logger.exception(
                "flood: не удалось привести сцену %s к сетке анализа",
                scene.get("id"),
            )
            rejected.append({**_scene_meta(scene), "reason": "warp_error"})
    return {"entries": entries, "rejected": rejected}


async def _scene_scl_stats(
    scene: dict[str, Any],
    bbox: list[float],
) -> dict[str, Any] | None:
    """Оценивает снег/облачность/валидность сцены по SCL (уменьшенное чтение).

    Для решения «годится ли окно как база» полные каналы не нужны: читается
    только SCL, приведённый к сетке 96×96 (nearest), а полный композит
    считается уже для выбранного окна.
    """
    scl_url = scene.get("scl_url")
    if not scl_url:
        return None
    scl_scale, scl_offset, _ = _band_scaling(scene, "scl", SCL_SCALE)
    bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
    try:
        scl, _ = await asyncio.to_thread(
            _read_band_window,
            scl_url,
            bbox_tuple,
            scl_scale,
            _SCL_PROBE_SHAPE,
            scl_offset,
        )
    except Exception:  # noqa: BLE001 — оценка базы не должна ронять сценарий
        logger.warning(
            "flood: не удалось оценить SCL сцены %s", scene.get("id"), exc_info=True
        )
        return None
    scl_int = np.rint(scl).astype("int16")
    if scl_int.size == 0:
        return None
    return {
        "snowShare": float((scl_int == _SCL_SNOW).mean()),
        "cloudShare": float(np.isin(scl_int, (8, 9, 10)).mean()),
        "validShare": float(np.isin(scl_int, _VALID_SCL).mean()),
    }


async def _select_baseline(
    event: date,
    bbox: list[float],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Подбирает ближайшее к событию окно базового периода без снега.

    Окна перебираются от ближнего к дальнему. Окно принимается, если снега в
    нём не больше ``SNOW_MAX_SHARE`` и достаточно валидных наблюдений; иначе
    «постоянную воду» отделить нельзя и любая вода на дату события выглядела бы
    затоплением. Попытки сохраняются в диагностике: пользователь видит, какие
    окна проверены и почему отклонены.
    """
    attempts: list[dict[str, Any]] = []
    diagnostics["baselineCandidates"] = attempts
    for days_to, days_from in BASELINE_WINDOWS:
        window_to = event - timedelta(days=days_to)
        if window_to <= _S2_L2A_START:
            attempts.append(
                {
                    "from": None,
                    "to": window_to.isoformat(),
                    "status": "before_archive",
                }
            )
            continue
        window_from = max(event - timedelta(days=days_from), _S2_L2A_START)
        scenes = await search_sentinel2_scenes(
            bbox,
            window_from.isoformat(),
            window_to.isoformat(),
            max_cloud_cover=60,
            limit=BASELINE_SCENES_PER_WINDOW,
        )
        attempt: dict[str, Any] = {
            "from": window_from.isoformat(),
            "to": window_to.isoformat(),
            "scenesCount": len(scenes),
            "sceneDates": [str(scene.get("datetime") or "")[:10] for scene in scenes],
        }
        attempts.append(attempt)
        if not scenes:
            attempt["status"] = "no_scenes"
            continue
        stats = [
            stat
            for stat in [
                await _scene_scl_stats(scene, bbox)
                for scene in scenes[:_BASELINE_PROBE_SCENES]
            ]
            if stat is not None
        ]
        if not stats:
            attempt["status"] = "scl_unreadable"
            continue
        snow = float(np.mean([stat["snowShare"] for stat in stats]))
        valid = float(np.mean([stat["validShare"] for stat in stats]))
        attempt["snowShare"] = round(snow, 4)
        attempt["cloudShare"] = round(
            float(np.mean([stat["cloudShare"] for stat in stats])), 4
        )
        attempt["validShare"] = round(valid, 4)
        if snow > SNOW_MAX_SHARE:
            attempt["status"] = "snow"
            continue
        if valid < BASELINE_MIN_VALID:
            attempt["status"] = "low_valid"
            continue
        attempt["status"] = "accepted"
        return {
            "accepted": True,
            "window": {
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
            },
            "scenes": scenes,
            "attempts": attempts,
        }
    return {"accepted": False, "window": None, "scenes": [], "attempts": attempts}


async def _resolve_area(
    state: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Территория: подтверждённая AOI пользователя либо Researcher."""
    resolved = _as_dict(context.get("resolved_territory"))
    geometry = resolved.get("geometry")
    raw_bbox = resolved.get("bbox")
    name = str(resolved.get("displayName") or resolved.get("name") or "").strip()
    if isinstance(geometry, dict) and isinstance(raw_bbox, list) and len(raw_bbox) == 4:
        return {
            "error": None,
            "context": context,
            "geometry": geometry,
            "bbox": [float(value) for value in raw_bbox],
            "name": name or "Территория",
        }
    outcome = await researcher_node({**state, "context": context})
    context = {**context, **_as_dict(outcome.get("context"))}
    territories = [
        item for item in _as_list(context.get("territories")) if isinstance(item, dict)
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
    return {
        "error": None,
        "context": context,
        "geometry": aoi.get("geometry"),
        "bbox": [float(value) for value in aoi["bbox"]],
        "name": str(aoi.get("displayName") or name or "Территория"),
    }


def _accepted_meta(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for scene in scenes:
        meta = _scene_meta(scene)
        meta["acquiredAt"] = meta.get("datetime")
        meta["resolutionM"] = GRID_RESOLUTION_M
        accepted.append(meta)
    return accepted


async def run_flood_extent(state: dict[str, Any]) -> dict[str, Any]:
    """Полный расчёт сценария «зоны затопления» (оптика, дата события)."""
    context = _as_dict(state.get("context"))
    query_text = str(state.get("query_text") or "")
    event = parse_event_date(query_text)

    if event is None:
        return _fail(
            context,
            "Не указана дата события. Затопление — краткосрочное явление: "
            "укажите дату (например, «5 апреля 2026») или подтвердите период "
            "события, чтобы не подменять его сезонным композитом.",
            stage="input_validation",
        )
    if event > date.today():
        return _fail(
            context,
            f"Дата события {event.isoformat()} ещё не наступила: наблюдений нет.",
            stage="input_validation",
        )

    area = await _resolve_area(state, context)
    context = _as_dict(area.get("context")) or context
    if area.get("error"):
        return _fail(
            context,
            str(area["error"]),
            stage=str(area.get("stage") or "territory_selection"),
        )
    name = str(area["name"])
    bbox = [float(value) for value in area["bbox"]]
    raw_geometry = area.get("geometry")
    geometry = raw_geometry if isinstance(raw_geometry, dict) else None
    west, south, east, north = bbox
    if east - west > 0.6 or north - south > 0.6:
        return _fail(
            context,
            "Территория слишком большая для анализа затопления (30 м): "
            f"{east - west:.2f}°×{north - south:.2f}°. Выберите участок.",
            stage="aoi_size",
        )

    event_from = (event - timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    event_to = min(
        (event + timedelta(days=EVENT_WINDOW_DAYS)).isoformat(),
        date.today().isoformat(),
    )

    target = target_grid(bbox, GRID_RESOLUTION_M)
    diagnostics: dict[str, Any] = {
        "analysisType": ANALYSIS_TYPE,
        "method": {
            "name": METHOD_NAME,
            "formula": METHOD_FORMULA,
            "resolutionM": GRID_RESOLUTION_M,
            "sensor": "Sentinel-2 MSI (L2A)",
            "radar": (
                "Sentinel-1 не использован: в каталоге доступны только GRD "
                "амплитуды без калибровки и террейн-коррекции — пороги "
                "обратного рассеяния недостоверны"
            ),
        },
        "eventDate": event.isoformat(),
        "eventWindow": {"from": event_from, "to": event_to},
        "grid": {
            "crs": target["crs"],
            "shape": target["shape"],
            "resolution": target["resolution"],
        },
    }

    event_scenes = await search_sentinel2_scenes(
        bbox, event_from, event_to, max_cloud_cover=60, limit=MAX_SCENES_PER_PERIOD
    )
    diagnostics["dates"] = {
        "event": [str(scene.get("datetime") or "")[:10] for scene in event_scenes],
        "baseline": [],
    }
    diagnostics["scenes"] = {
        "event": _accepted_meta(event_scenes),
        "baseline": [],
        "rejected": [],
    }
    if not event_scenes:
        return _fail(
            context,
            "Нет доступных сцен Sentinel-2 в окне события "
            f"({event_from}…{event_to}). Затопление не оценивалось.",
            category="data_quality",
            stage="stac_search",
            diagnostics=diagnostics,
        )

    baseline_pick = await _select_baseline(event, bbox, diagnostics)
    baseline_scenes: list[dict[str, Any]] = baseline_pick["scenes"]
    baseline_window = baseline_pick.get("window")
    if not baseline_pick["accepted"] or not isinstance(baseline_window, dict):
        checked = ", ".join(
            f"{attempt.get('from') or '—'}…{attempt.get('to')} "
            f"({attempt.get('status')}"
            + (
                f", снег {float(attempt['snowShare']):.0%}"
                if isinstance(attempt.get("snowShare"), (int, float))
                else ""
            )
            + ")"
            for attempt in baseline_pick["attempts"]
        )
        reason = (
            "Не найдено бесснежного базового периода до события "
            f"({event.isoformat()}): под снегом постоянную воду нельзя "
            "отделить от временного затопления. Проверенные окна: "
            f"{checked or '—'}. Затопление не оценивалось."
        )
        diagnostics["baselineWindow"] = None
        diagnostics["baselineSnowShare"] = None
        diagnostics["interpretability"] = {
            "status": "insufficient",
            "minCoverageThreshold": MIN_SHARED_COVERAGE,
            "reasons": [reason],
        }
        return _fail(
            context,
            reason,
            category="data_quality",
            stage="baseline_quality",
            diagnostics=diagnostics,
        )
    base_from = str(baseline_window["from"])
    base_to = str(baseline_window["to"])
    diagnostics["baselineWindow"] = {"from": base_from, "to": base_to}
    diagnostics["dates"]["baseline"] = [
        str(scene.get("datetime") or "")[:10] for scene in baseline_scenes
    ]
    diagnostics["scenes"]["baseline"] = _accepted_meta(baseline_scenes)

    event_read = await _read_period(event_scenes, bbox, target)
    baseline_read = await _read_period(baseline_scenes, bbox, target)
    diagnostics["scenes"]["rejected"] = [
        *event_read["rejected"],
        *baseline_read["rejected"],
    ]
    if not event_read["entries"] or not baseline_read["entries"]:
        return _fail(
            context,
            "Не удалось прочитать сцены события или базового периода.",
            category="analysis_error",
            stage="scene_read",
            diagnostics=diagnostics,
        )
    event_water = combine_water(event_read["entries"])
    baseline_water = combine_water(baseline_read["entries"])

    shape = (int(target["shape"][0]), int(target["shape"][1]))
    transform = affine_of(target)
    crs = target["crs"]
    aoi = _aoi_mask(geometry, transform, shape, crs)
    shared = event_water["valid"] & baseline_water["valid"] & aoi
    aoi_pixels = int(aoi.sum())
    shared_pixels = int(shared.sum())
    coverage_ratio = (shared_pixels / aoi_pixels) if aoi_pixels else 0.0
    pixel_area_m2 = float(target.get("pixelAreaM2") or 0.0)
    analysis_area_km2 = aoi_pixels * pixel_area_m2 / 1_000_000.0
    shared_area_km2 = shared_pixels * pixel_area_m2 / 1_000_000.0
    coverage = {
        "aoiPixels": aoi_pixels,
        "sharedValidPixels": shared_pixels,
        "sharedValid": round(coverage_ratio, 6),
        "unknown": round(1.0 - coverage_ratio, 6) if aoi_pixels else 1.0,
        "analysisAreaKm2": round(analysis_area_km2, 6),
        "sharedValidAreaKm2": round(shared_area_km2, 6),
        "unknownAreaKm2": round(max(0.0, analysis_area_km2 - shared_area_km2), 6),
        "minCoverageThreshold": MIN_SHARED_COVERAGE,
        "note": (
            f"покрытие по пикселям сетки {GRID_RESOLUTION_M:.0f} м внутри границы "
            "территории; вне покрытия затопление не оценивалось"
        ),
    }
    diagnostics["coverage"] = coverage
    diagnostics["classification"] = {
        "method": METHOD_NAME,
        "formula": METHOD_FORMULA,
        "mndwiThreshold": MNDWI_THRESHOLD,
        "minAreaM2": MIN_AREA_M2,
        "validSclClasses": list(_VALID_SCL),
        "waterRule": "вода в ≥ половине валидных сцен периода (MNDWI > 0 и NIR < 0.15)",
    }

    limitations = [
        "Затопление определяется по MNDWI (оптика): при сплошной облачности "
        "событие не наблюдается, такие пиксели попадают в «неизвестно».",
        "Sentinel-1 не использован: доступные GRD-амплитуды без калибровки и "
        "террейн-коррекции не дают достоверных порогов обратного рассеяния.",
        "«Постоянная вода» — вода в базовом периоде "
        f"({base_from}…{base_to}), а не глобальный атлас постоянных водоёмов.",
        "Снег и лёд (SCL 11) исключены из валидных наблюдений: под снегом "
        "вода не распознаётся, такие пиксели считаются «неизвестно», а не "
        "«воды нет».",
        "Глубина воды, ущерб и опасность не рассчитываются и не оцениваются.",
        "Между датами события и базового периода возможны сезонные изменения "
        "водности; результат чувствителен к выбору базового периода.",
    ]
    diagnostics["limitations"] = limitations
    diagnostics["technicalSuccess"] = True
    diagnostics["dataQualityAssessed"] = True
    baseline_snow = (
        sum(entry.get("snowShare", 0.0) for entry in baseline_read["entries"])
        / max(1, len(baseline_read["entries"]))
    )
    diagnostics["baselineSnowShare"] = round(float(baseline_snow), 4)
    if baseline_snow > SNOW_MAX_SHARE:
        # Снег/лёд в базовом периоде: «постоянную воду» отделить нельзя, а без
        # неё любая вода на дату события выглядела бы затоплением.
        reason = (
            f"Базовый период ({base_from}…{base_to}) на {baseline_snow:.0%} под "
            "снегом/льдом: постоянную воду нельзя отделить от временного "
            "затопления. Укажите дату события в бесснежный период."
        )
        diagnostics["baselineReliable"] = False
        diagnostics["interpretability"] = {
            "status": "insufficient",
            "minCoverageThreshold": MIN_SHARED_COVERAGE,
            "reasons": [reason],
        }
        diagnostics["limitations"] = [*limitations, reason]
        context["analysis_diagnostics"] = diagnostics
        context["datasets"] = {
            "accepted": _accepted_meta([*event_scenes, *baseline_scenes]),
            "rejected": diagnostics["scenes"]["rejected"],
        }
        return _fail(
            context,
            reason,
            category="data_quality",
            stage="baseline_quality",
            diagnostics=diagnostics,
        )
    diagnostics["baselineReliable"] = True

    if coverage_ratio < MIN_SHARED_COVERAGE:
        reason = (
            f"Общее валидное покрытие {coverage_ratio:.1%} ниже порога "
            f"{MIN_SHARED_COVERAGE:.0%}: затопление не интерпретируется."
        )
        diagnostics["interpretability"] = {
            "status": "insufficient",
            "minCoverageThreshold": MIN_SHARED_COVERAGE,
            "reasons": [reason],
        }
        diagnostics["limitations"] = [*limitations, reason]
        context["analysis_diagnostics"] = diagnostics
        context["datasets"] = {
            "accepted": _accepted_meta([*event_scenes, *baseline_scenes]),
            "rejected": diagnostics["scenes"]["rejected"],
        }
        return _fail(
            context,
            reason,
            category="data_quality",
            stage="coverage",
            diagnostics=diagnostics,
        )

    permanent = event_water["water"] & baseline_water["water"] & shared
    flood = event_water["water"] & ~baseline_water["water"] & shared
    receded = ~event_water["water"] & baseline_water["water"] & shared
    zones = build_change_zones(
        {"retained": permanent, "lost": receded, "new": flood},
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
        "Затопление (MNDWI, Sentinel-2) на дату "
        f"{event.isoformat()}: временное затопление — зон {counts['new']}, "
        f"постоянная вода — зон {counts['retained']}, вода ушла — зон "
        f"{counts['lost']}. Покрытие территории: {coverage_ratio:.1%}. "
        f"База для сравнения: {base_from}…{base_to}."
    )
    explanation = (
        f"Окно события: {event_from}…{event_to} (сцены: "
        f"{', '.join(diagnostics['dates']['event']) or '—'}). Базовый период: "
        f"{base_from}…{base_to} (сцены: "
        f"{', '.join(diagnostics['dates']['baseline']) or '—'}, снег "
        f"{baseline_snow:.0%}). "
        "Временное затопление — вода на дату события, которой не было до "
        "события; постоянная вода — вода в обоих периодах. Глубина и ущерб не "
        "оцениваются. Площади зон рассчитаны PostGIS по итоговым полигонам."
    )
    quality_reasons: list[str] = []
    if coverage_ratio < LIMITED_COVERAGE:
        quality_reasons.append(
            f"Общее валидное покрытие {coverage_ratio:.1%} ниже "
            f"{LIMITED_COVERAGE:.0%}: часть территории не наблюдена, "
            "вне покрытия затопление не оценивалось."
        )
    if baseline_snow > SNOW_WARN_SHARE:
        quality_reasons.append(
            f"В базовом периоде {baseline_snow:.0%} снега/льда: часть "
            "различий может объясняться сезоном, а не паводком."
        )
    diagnostics["interpretability"] = {
        "status": "limited" if quality_reasons else "sufficient",
        "minCoverageThreshold": MIN_SHARED_COVERAGE,
        "reasons": quality_reasons,
    }
    if quality_reasons:
        diagnostics["limitations"] = [*limitations, *quality_reasons]
    diagnostics["zonesCount"] = counts
    context["analysis_diagnostics"] = diagnostics
    context["analysis_failure"] = None
    context["territories"] = [{"name": name}]
    context["change_zones"] = zones
    context["aoi_geometry"] = geometry
    context["period"] = {"start": event.isoformat(), "end": event.isoformat()}
    context["datasets"] = {
        "accepted": _accepted_meta([*event_scenes, *baseline_scenes]),
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
        "visualization": {"summary_text": summary, "explanation_text": explanation},
        "validation": {"outcome": "passed", "category": "none", "reasons": []},
        "messages": [summary],
        "errors": [],
    }
