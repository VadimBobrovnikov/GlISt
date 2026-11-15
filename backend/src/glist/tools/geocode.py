"""Геокодирование территории через Nominatim/OSM (задача T050).

- обязательный User-Agent из ``OSM_USER_AGENT``;
- троттлинг не чаще 1 запроса в секунду;
- кэш результатов в таблице ``geocode_cache`` (TTL 30 дней).
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from glist.config import get_settings
from glist.db.models import GeocodeCache
from glist.db.session import get_session_ctx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PROVIDER = "nominatim"
CACHE_TTL_DAYS = 30
_MIN_INTERVAL_SECONDS = 1.0
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Типы Nominatim, которые описывают территорию, а не здание/точку.
_AREA_TYPES = frozenset(
    {
        "city",
        "town",
        "village",
        "municipality",
        "administrative",
        "state",
        "region",
        "county",
        "district",
        "province",
    }
)
# Более конкретные места: их выбираем при наличии родительского города.
_SPECIFIC_TYPES = frozenset(
    {
        "suburb",
        "neighbourhood",
        "quarter",
        "city_district",
        "borough",
        "locality",
        "hamlet",
        "village",
        "isolated_dwelling",
        "district",
    }
)
_PARENT_ONLY_TYPES = frozenset({"city", "town", "state", "region", "municipality"})


class GeocodeError(RuntimeError):
    """Сбой геокодера: сеть, HTTP 429/403, ошибка разбора ответа."""


# Категории объектов, которые различает пользователь.
_OBJECT_TYPES = frozenset(
    {"city", "district", "microdistrict", "quarter", "island", "park", "other"}
)
_KIND_BY_OSM = {
    "city": "city",
    "town": "city",
    "municipality": "city",
    "administrative": "district",
    "district": "district",
    "city_district": "district",
    "borough": "district",
    "suburb": "microdistrict",
    "neighbourhood": "microdistrict",
    "quarter": "microdistrict",
    "locality": "microdistrict",
    "hamlet": "microdistrict",
    "village": "microdistrict",
    "island": "island",
    "islet": "island",
    "archipelago": "island",
    "park": "park",
    "garden": "park",
    "nature_reserve": "park",
    "protected_area": "park",
    "forest": "park",
}
_EXPECTED_KINDS = {
    "city": frozenset({"city"}),
    "district": frozenset({"district", "microdistrict"}),
    "microdistrict": frozenset({"microdistrict", "district"}),
    "quarter": frozenset({"microdistrict", "district"}),
    "island": frozenset({"island"}),
    "park": frozenset({"park"}),
    "other": frozenset({"city", "district", "microdistrict", "island", "park"}),
}
_TYPE_WORDS = (
    "микрорайон",
    "район",
    "квартал",
    "остров",
    "парк",
    "город",
    "поселок",
    "посёлок",
    "село",
)


def normalize_object_type(value: str | None) -> str:
    """Приводит тип объекта к поддерживаемой категории (иначе ``other``)."""
    if not isinstance(value, str):
        return "other"
    key = value.strip().lower().replace("ё", "е")
    aliases = {
        "город": "city",
        "city": "city",
        "район": "district",
        "district": "district",
        "административный район": "district",
        "микрорайон": "microdistrict",
        "microdistrict": "microdistrict",
        "квартал": "quarter",
        "quarter": "quarter",
        "остров": "island",
        "island": "island",
        "парк": "park",
        "park": "park",
    }
    return aliases.get(key, "other")


def normalize_name(value: str) -> str:
    """Нормализация названия для сравнения (регистр, ё, дефисы, типы)."""
    lowered = value.strip().lower().replace("ё", "е")
    lowered = lowered.replace("-", " ").replace("_", " ")
    for word in _TYPE_WORDS:
        lowered = re.sub(rf"\b{re.escape(word)}\b", " ", lowered)
    return " ".join(lowered.split())

_rate_lock = asyncio.Lock()
_last_request_at = 0.0


def _cache_key(
    name: str,
    parent: str | None = None,
    object_type: str | None = None,
) -> str:
    """Ключ кэша: имя, родитель, тип, страна и версия формата."""
    key = "|".join(
        [
            " ".join(name.strip().lower().split()),
            " ".join((parent or "").strip().lower().split()),
            normalize_object_type(object_type),
        ]
    )
    if _CYRILLIC.search(name + (parent or "")):
        return f"{key}|ru|poly|v2"
    return f"{key}|poly|v2"


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def _area_of(item: dict[str, Any]) -> float:
    bbox = _bbox_from_boundingbox(item.get("boundingbox"))
    if bbox is None:
        return 0.0
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _territory_payload(
    item: dict[str, Any],
    parent: str | None,
) -> dict[str, Any]:
    display = str(item.get("display_name") or "")
    name = display.split(",")[0].strip() or display
    return {
        "name": name,
        "displayName": display,
        "addresstype": str(item.get("addresstype") or item.get("type") or ""),
        "bbox": _bbox_from_boundingbox(item.get("boundingbox")),
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "parent": parent,
    }


def _pick_territory(
    data: list[Any],
    query: str,
    parent: str | None = None,
) -> dict[str, Any] | None:
    """Выбирает территорию: район при наличии родителя, иначе админ-объект.

    При запросе района нельзя возвращать весь город: результат обязан
    содержать родителя в адресе, а тип должен быть конкретным (suburb,
    neighbourhood, city_district и т.п.).
    """
    candidates = [
        item
        for item in data
        if isinstance(item, dict)
        and _bbox_from_boundingbox(item.get("boundingbox")) is not None
    ]
    if not candidates:
        return None

    ambiguous = False
    if parent:
        parent_key = _normalize_text(parent)
        matched = [
            item
            for item in candidates
            if parent_key
            in _normalize_text(str(item.get("display_name") or ""))
        ]
        if not matched:
            return None
        specific = [
            item
            for item in matched
            if str(item.get("addresstype") or item.get("type") or "").lower()
            in _SPECIFIC_TYPES
        ]
        pool = specific or [
            item
            for item in matched
            if str(item.get("addresstype") or item.get("type") or "").lower()
            not in _PARENT_ONLY_TYPES
        ]
        if not pool:
            return None
        best = min(pool, key=_area_of)
        best_type = str(
            best.get("addresstype") or best.get("type") or ""
        ).lower()
        if best_type in _PARENT_ONLY_TYPES:
            return None
        ambiguous = len(specific) > 1
    else:
        administrative = [
            item
            for item in candidates
            if str(item.get("addresstype") or item.get("type") or "").lower()
            in _AREA_TYPES
        ]
        pool = administrative or candidates
        best = max(pool, key=_area_of)

    bbox = _bbox_from_boundingbox(best.get("boundingbox"))
    if bbox is None:
        return None
    payload = _territory_payload(best, parent)
    payload.update(
        {
            "bbox": bbox,
            "provider": PROVIDER,
            "ambiguous": ambiguous,
            "candidates": [
                _territory_payload(item, parent) for item in pool[:8]
            ],
        }
    )
    geometry = best.get("geojson")
    if (
        isinstance(geometry, dict)
        and geometry.get("type") in ("Polygon", "MultiPolygon")
        and isinstance(geometry.get("coordinates"), list)
    ):
        payload["geometry"] = geometry
    return payload


def _payload_bbox(payload: object) -> list[float] | None:
    """Извлекает [west, south, east, north] из сохранённого payload."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        values = [float(item) for item in raw]
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _bbox_from_boundingbox(value: Any) -> list[float] | None:
    """Nominatim отдаёт [south, north, west, east] → [west, south, east, north]."""
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        south, north, west, east = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if west >= east or south >= north:
        return None
    return [west, south, east, north]


async def _throttle() -> None:
    """Выдерживает паузу не менее 1 секунды между запросами."""
    global _last_request_at
    async with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        wait = _MIN_INTERVAL_SECONDS - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()



def _candidate_kind(item: dict[str, Any]) -> str:
    for key in (
        str(item.get("addresstype") or "").lower(),
        str(item.get("type") or "").lower(),
        str(item.get("class") or "").lower(),
    ):
        if key in _KIND_BY_OSM:
            return _KIND_BY_OSM[key]
    return "other"


def _candidate_city(item: dict[str, Any]) -> str | None:
    address = item.get("address")
    if not isinstance(address, dict):
        return None
    for key in ("city", "town", "village", "municipality", "county", "state"):
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _candidate_from_osm(
    item: dict[str, Any],
    parent: str | None,
) -> dict[str, Any]:
    display = str(item.get("display_name") or "")
    name = str(item.get("name") or display.split(",")[0]).strip()
    bbox = _bbox_from_boundingbox(item.get("boundingbox"))
    geometry = item.get("geojson")
    if not (
        isinstance(geometry, dict)
        and geometry.get("type") in ("Polygon", "MultiPolygon")
        and isinstance(geometry.get("coordinates"), list)
    ):
        geometry = None
    return {
        "name": name,
        "displayName": display,
        "kind": _candidate_kind(item),
        "osmType": str(item.get("type") or ""),
        "addresstype": str(item.get("addresstype") or item.get("type") or ""),
        "city": _candidate_city(item),
        "parent": parent,
        "bbox": bbox,
        "geometry": geometry,
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "pointOnly": geometry is None and item.get("lat") is not None,
        "provider": PROVIDER,
    }


def _name_similarity(candidate: dict[str, Any], requested: str) -> float:
    left = normalize_name(str(candidate.get("name") or ""))
    right = normalize_name(requested)
    if not left or not right:
        return 0.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens | right_tokens
        else 0.0
    )
    if right in normalize_name(str(candidate.get("displayName") or "")):
        return max(ratio, overlap, 0.95)
    return max(ratio, overlap)


def _parent_match(
    candidate: dict[str, Any],
    parent: str,
    parent_bbox: list[float] | None,
) -> bool:
    parent_norm = normalize_name(parent)
    if not parent_norm:
        return True
    display = normalize_name(str(candidate.get("displayName") or ""))
    if parent_norm in display:
        return True
    city = candidate.get("city")
    if isinstance(city, str) and normalize_name(city) and (
        parent_norm in normalize_name(city)
        or normalize_name(city) in parent_norm
    ):
        return True
    if parent_bbox is not None:
        lat_value = candidate.get("lat")
        lon_value = candidate.get("lon")
        if lat_value is None or lon_value is None:
            return False
        try:
            lat = float(lat_value)
            lon = float(lon_value)
        except (TypeError, ValueError):
            return False
        west, south, east, north = parent_bbox
        return west <= lon <= east and south <= lat <= north
    return False


def select_territory(
    data: list[Any],
    name: str,
    object_type: str | None,
    parent: str | None,
    parent_bbox: list[float] | None = None,
) -> dict[str, Any] | None:
    """Выбирает кандидата по названию, типу и родителю (не по площади)."""
    requested_type = normalize_object_type(object_type)
    expected = _EXPECTED_KINDS[requested_type]
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_osm(item, parent)
        if candidate["bbox"] is None:
            continue
        kind = str(candidate.get("kind") or "other")
        if requested_type != "other" and kind not in expected:
            continue
        if parent is not None and not _parent_match(candidate, parent, parent_bbox):
            continue
        similarity = _name_similarity(candidate, name)
        if similarity < 0.45:
            continue
        type_score = 1.0 if kind in expected else 0.5
        parent_score = 1.0 if parent is None else 1.0
        geometry_bonus = 0.05 if candidate.get("geometry") else 0.0
        score = (
            0.6 * similarity
            + 0.25 * type_score
            + 0.15 * parent_score
            + geometry_bonus
        )
        scored.append((score, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    ambiguous = (
        len(scored) > 1
        and scored[1][0] >= best_score - 0.05
        and scored[1][1].get("kind") == best.get("kind")
    )
    result = dict(best)
    result.update(
        {
            "requestedName": name,
            "requestedType": requested_type,
            "score": round(best_score, 4),
            "ambiguous": ambiguous,
            "candidates": [candidate for _, candidate in scored[:8]],
        }
    )
    return result

async def _fetch_nominatim(
    query: str,
) -> list[Any] | None:
    """Запрашивает Nominatim и возвращает список кандидатов."""
    settings = get_settings()
    headers = {
        "User-Agent": settings.osm_user_agent,
        "Accept": "application/json",
    }
    params: dict[str, str | int] = {
        "q": query,
        "format": "jsonv2",
        "limit": 10,
        "addressdetails": 1,
        "polygon_geojson": 1,
        "polygon_threshold": "0.0002",
    }
    if _CYRILLIC.search(query):
        # «Казани» без фильтра находит одноимённый населённый пункт в Италии.
        params["countrycodes"] = "ru"

    try:
        await _throttle()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                NOMINATIM_URL,
                params=params,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.warning("Nominatim request failed for %s: %s", query, exc)
        raise GeocodeError(f"network: {exc}") from exc

    if response.status_code != 200:
        logger.warning("Nominatim HTTP %s for %s", response.status_code, query)
        raise GeocodeError(f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("Nominatim invalid JSON for %s: %s", query, exc)
        raise GeocodeError("invalid_json") from exc
    if not isinstance(data, list) or not data:
        return None
    return data


async def geocode_territory(
    name: str,
    *,
    parent: str | None = None,
    object_type: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Возвращает территорию по имени, типу и родительскому городу.

    Кандидаты ранжируются по сходству названия, совпадению типа и
    принадлежности родителю; площадь не является критерием выбора.
    """
    query = name.strip()
    if not query:
        return None
    parent_name = parent.strip() if isinstance(parent, str) and parent.strip() else None
    search_query = f"{query}, {parent_name}" if parent_name else query

    normalized = _cache_key(query, parent_name, object_type)
    now = datetime.now(UTC)

    if use_cache:
        try:
            async with get_session_ctx() as session:
                result = await session.execute(
                    select(GeocodeCache).where(
                        GeocodeCache.query_normalized == normalized,  # type: ignore[arg-type]
                        GeocodeCache.provider == PROVIDER,  # type: ignore[arg-type]
                        GeocodeCache.expires_at > now,  # type: ignore[arg-type]
                    )
                )
                cached = result.scalars().first()
                if (
                    cached is not None
                    and isinstance(cached.payload, dict)
                    and cached.payload.get("requestedType")
                    == normalize_object_type(object_type)
                ):
                    return cached.payload
        except Exception as exc:  # noqa: BLE001 — кэш не должен ломать геокодинг
            logger.warning("Geocode cache read failed: %s", exc)

    data = await _fetch_nominatim(search_query)
    if data is None:
        return None
    parent_bbox: list[float] | None = None
    if parent_name and parent_name != query:
        try:
            parent_payload = await geocode_territory(
                parent_name,
                parent=None,
                object_type="city",
                use_cache=True,
            )
        except GeocodeError as exc:
            logger.warning("Geocode parent lookup failed: %s", exc)
            parent_payload = None
        if isinstance(parent_payload, dict):
            parent_bbox = _payload_bbox(parent_payload)
    payload = select_territory(
        data,
        query,
        object_type,
        parent_name,
        parent_bbox,
    )
    if payload is None:
        return None

    if use_cache:
        try:
            async with get_session_ctx() as session:
                existing = await session.execute(
                    select(GeocodeCache).where(
                        GeocodeCache.query_normalized == normalized,  # type: ignore[arg-type]
                        GeocodeCache.provider == PROVIDER,  # type: ignore[arg-type]
                    )
                )
                row = existing.scalars().first()
                if row is None:
                    row = GeocodeCache(
                        query_normalized=normalized,
                        provider=PROVIDER,
                        payload=payload,
                        geometry=None,
                        fetched_at=now,
                        expires_at=now + timedelta(days=CACHE_TTL_DAYS),
                    )
                    session.add(row)
                else:
                    row.payload = payload
                    row.fetched_at = now
                    row.expires_at = now + timedelta(days=CACHE_TTL_DAYS)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — кэш не должен ломать геокодинг
            logger.warning("Geocode cache write failed: %s", exc)

    return payload