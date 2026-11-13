"""Геокодирование территории через Nominatim/OSM (задача T050).

- обязательный User-Agent из ``OSM_USER_AGENT``;
- троттлинг не чаще 1 запроса в секунду;
- кэш результатов в таблице ``geocode_cache`` (TTL 30 дней).
"""

from __future__ import annotations

import asyncio
import logging
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

_rate_lock = asyncio.Lock()
_last_request_at = 0.0


def _normalize(name: str) -> str:
    """Нормализует запрос для ключа кэша."""
    return " ".join(name.strip().lower().split())


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


async def _fetch_nominatim(query: str) -> dict[str, Any] | None:
    """Запрашивает Nominatim и возвращает нормализованный результат."""
    settings = get_settings()
    headers = {
        "User-Agent": settings.osm_user_agent,
        "Accept": "application/json",
    }
    params: dict[str, str | int] = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 0,
    }

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
        return None

    if response.status_code != 200:
        logger.warning("Nominatim HTTP %s for %s", response.status_code, query)
        return None
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("Nominatim invalid JSON for %s: %s", query, exc)
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None

    first = data[0]
    bbox = _bbox_from_boundingbox(first.get("boundingbox"))
    if bbox is None:
        return None
    return {
        "name": str(first.get("display_name") or query),
        "bbox": bbox,
        "lat": first.get("lat"),
        "lon": first.get("lon"),
        "provider": PROVIDER,
    }


async def geocode_territory(
    name: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Возвращает bbox территории по её имени.

    Args:
        name: Название территории из запроса.
        use_cache: Использовать таблицу ``geocode_cache``.

    Returns:
        Словарь с ``name``, ``bbox`` [west, south, east, north], ``lat``,
        ``lon``, ``provider`` или ``None``, если геокодирование не удалось.
    """
    query = name.strip()
    if not query:
        return None

    normalized = _normalize(query)
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
                if cached is not None and isinstance(cached.payload, dict):
                    return cached.payload
        except Exception as exc:  # noqa: BLE001 — кэш не должен ломать геокодинг
            logger.warning("Geocode cache read failed: %s", exc)

    payload = await _fetch_nominatim(query)
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