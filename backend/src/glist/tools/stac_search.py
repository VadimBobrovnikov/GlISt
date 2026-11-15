"""STAC search tool for Sentinel-2 and Landsat imagery (T049).

Provides functions to search for satellite imagery via STAC API
using pystac-client, with cloud cover filtering and metadata extraction.
"""
import asyncio
import json
import logging
import os
from typing import Any

import pystac
import pystac_client
import requests

logger = logging.getLogger(__name__)

# Element 84 Earth Search v1: публичный STAC API Sentinel-2 на AWS.
EARTH_SEARCH_STAC_URL = "https://earth-search.aws.element84.com/v1"
SENTINEL2_L2A_COLLECTION = "sentinel-2-l2a"
_STAC_ATTEMPTS = 2
_STAC_TIMEOUT_SECONDS = 45.0
_STAC_BACKOFF_SECONDS = 2.0


def search_sentinel2(
    bbox: list[float],
    datetime_range: str,
    max_cloud_cover: int = 20,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search Sentinel-2 scenes via STAC API.

    Args:
        bbox: Bounding box [minx, miny, maxx, maxy] in EPSG:4326.
        datetime_range: ISO datetime range string (e.g., "2024-01-01/2024-01-31").
        max_cloud_cover: Maximum cloud cover percentage to include (default: 20).
        limit: Maximum number of results to return (default: 10).

    Returns:
        List of dictionaries with scene metadata:
        - id: STAC item ID
        - datetime: Acquisition date
        - cloud_cover: Percentage cloud cover
        - cog_url: URL to Cloud-Optimized GeoTIFF
        - platform: Satellite platform (Sentinel-2A or Sentinel-2B)
    """
    try:
        client = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/stac/v1"
        )

        search = client.search(
            collections=["sentinel-2"],
            bbox=bbox,
            datetime=datetime_range,
            query={"cloud_cover": {"lt": max_cloud_cover}},
            limit=limit,
        )

        results = []
        for item in getattr(search, "items", []):
            properties = item.properties
            # Extract S2 band URLs - look for B01-B11 or similar
            assets = item.assets
            cog_url = None
            if "analysis" in assets:
                analysis_asset = assets["analysis"]
                if "href" in analysis_asset:
                    cog_url = analysis_asset["href"]

            scene_data = {
                "id": item.id,
                "datetime": properties.get("datetime"),
                "cloud_cover": properties.get("cloud_cover", 0),
                "cog_url": cog_url,
                "platform": properties.get("platform", "Sentinel-2"),
            }
            results.append(scene_data)

        return results

    except Exception as e:
        # Return empty list on error rather than failing hard
        print(f"STAC search error: {e}")
        return []


def search_landsat(
    bbox: list[float],
    datetime_range: str,
    max_cloud_cover: int = 20,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search Landsat scenes via STAC API.

    Args:
        bbox: Bounding box [minx, miny, maxx, maxy] in EPSG:4326.
        datetime_range: ISO datetime range string.
        max_cloud_cover: Maximum cloud cover percentage (default: 20).
        limit: Maximum number of results (default: 10).

    Returns:
        List of dictionaries with Landsat scene metadata.
    """
    try:
        client = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/stac/v1"
        )

        search = client.search(
            collections=["landsat-c2-l2"],
            bbox=bbox,
            datetime=datetime_range,
            query={"cloud_cover": {"lt": max_cloud_cover}},
            limit=limit,
        )

        results = []
        for item in getattr(search, "items", []):
            properties = item.properties
            assets = item.assets
            cog_url = None
            # Look for STB or B10 thermal band or similar
            for asset_name, asset in assets.items():
                if "TrueColor" in asset_name or "FalseColor" in asset_name and "href" in asset:
                    cog_url = asset["href"]
                    break

            scene_data = {
                "id": item.id,
                "datetime": properties.get("datetime"),
                "cloud_cover": properties.get("cloud_cover", 0),
                "cog_url": cog_url,
                "platform": properties.get("platform", "Landsat"),
            }
            results.append(scene_data)

        return results

    except Exception as e:
        print(f"Landsat STAC search error: {e}")
        return []


def filter_by_cloud_cover(
    scenes: list[dict[str, Any]], max_cloud_cover: int = 20
) -> list[dict[str, Any]]:
    """Filter scenes by maximum cloud cover percentage.

    Args:
        scenes: List of scene dicts from search_sentinel2 or search_landsat.
        max_cloud_cover: Maximum allowed cloud cover %.

    Returns:
        Filtered list of scenes within cloud cover threshold.
    """
    return [s for s in scenes if s.get("cloud_cover", 100) <= max_cloud_cover]


def extract_cog_url(scenes: list[dict[str, Any]]) -> list[str]:
    """Extract COG URLs from list of scenes.

    Args:
        scenes: List of scene dicts.

    Returns:
        List of COG URLs (may contain None values if no COG found).
    """
    return [s["cog_url"] for s in scenes if s.get("cog_url") is not None]


def preserve_provenance(item: pystac.Item) -> dict[str, Any]:
    """Extract provenance metadata from a STAC Item.

    Args:
        item: STAC Item instance.

    Returns:
        Dictionary with provenance information:
        - datetime: Acquisition datetime
        - cloud_cover: Cloud cover percentage
        - platform: Satellite platform
        - instrument: Sensor instrument
        - grid_id: Grid identifier
    """
    properties = item.properties
    return {
        "datetime": properties.get("datetime"),
        "cloud_cover": properties.get("cloud_cover", 0),
        "platform": properties.get("platform", "Unknown"),
        "instrument": properties.get("instrument", "Unknown"),
        "grid_id": properties.get("grid_id", None),
    }


def _asset_href(item: Any, *names: str) -> str | None:
    """Возвращает href первого найденного asset по именам (pystac Asset/dict)."""
    assets = getattr(item, "assets", None)
    if not isinstance(assets, dict) and isinstance(item, dict):
        assets = item.get("assets", {})
    if not isinstance(assets, dict):
        return None
    for name in names:
        asset = assets.get(name)
        if asset is None:
            continue
        href = getattr(asset, "href", None)
        if href is None and isinstance(asset, dict):
            href = asset.get("href")
        if isinstance(href, str) and href:
            return href
    return None


def _asset_scaling(
    item: dict[str, Any],
    *names: str,
) -> tuple[float | None, float | None]:
    """Scale/offset band из метаданных STAC-ассета (``raster:bands``)."""
    assets = item.get("assets")
    if not isinstance(assets, dict):
        return None, None
    for name in names:
        asset = assets.get(name)
        if not isinstance(asset, dict):
            continue
        bands = asset.get("raster:bands")
        if not isinstance(bands, list) or not bands:
            continue
        band = bands[0]
        if not isinstance(band, dict):
            continue
        raw_scale = band.get("scale")
        raw_offset = band.get("offset")
        scale = float(raw_scale) if isinstance(raw_scale, (int, float)) else None
        offset = float(raw_offset) if isinstance(raw_offset, (int, float)) else None
        return scale, offset
    return None, None


def _iter_search_items(search: Any) -> list[Any]:
    """Возвращает элементы поиска (совместимо с ItemSearch.items()/списком)."""
    raw_items = getattr(search, "items", [])
    if callable(raw_items):
        return list(raw_items())
    return list(raw_items or [])


def _rfc3339(value: str, *, end_of_day: bool = False) -> str:
    """Приводит дату к RFC3339, который требует Earth Search."""
    text = value.strip()
    if "T" in text:
        return text
    suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
    return f"{text}{suffix}"


def _search_sentinel2_scenes_sync(
    bbox: list[float],
    start_date: str,
    end_date: str,
    max_cloud_cover: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Синхронная часть поиска (pystac-client блокирующий)."""
    params: dict[str, str | int] = {
        "collections": SENTINEL2_L2A_COLLECTION,
        "bbox": ",".join(str(value) for value in bbox),
        "datetime": (
            f"{_rfc3339(start_date)}"
            f"/{_rfc3339(end_date, end_of_day=True)}"
        ),
        "limit": min(max(limit, 5), 20),
        "query": json.dumps({"eo:cloud_cover": {"lt": max_cloud_cover}}),
        # Без сортировки Earth Search отдаёт последние сцены, и ясные
        # более ранние (например, сентябрьские) теряются из-за limit.
        "sortby": "+properties.eo:cloud_cover",
    }
    headers = {
        "User-Agent": os.environ.get("OSM_USER_AGENT", "GlISt/0.1"),
        "Accept": "application/geo+json",
    }
    response = requests.get(
        f"{EARTH_SEARCH_STAC_URL}/search",
        params=params,
        headers=headers,
        timeout=(10, 60),
    )
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features", []) if isinstance(payload, dict) else []
    if not isinstance(features, list):
        return []

    scenes: list[dict[str, Any]] = []
    for item in features:
        if not isinstance(item, dict):
            continue
        red_url = _asset_href(item, "red", "B04", "b04")
        nir_url = _asset_href(item, "nir", "B08", "b08")
        scl_url = _asset_href(item, "scl", "SCL")
        if not (red_url and nir_url and scl_url):
            continue
        green_url = _asset_href(item, "green", "B03", "b03")
        blue_url = _asset_href(item, "blue", "B02", "b02")
        red_scale, red_offset = _asset_scaling(item, "red", "B04", "b04")
        green_scale, green_offset = _asset_scaling(item, "green", "B03", "b03")
        blue_scale, blue_offset = _asset_scaling(item, "blue", "B02", "b02")
        nir_scale, nir_offset = _asset_scaling(item, "nir", "B08", "b08")
        scl_scale, scl_offset = _asset_scaling(item, "scl", "SCL")

        properties = item.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        cloud_raw = properties.get(
            "eo:cloud_cover", properties.get("cloud_cover", 100)
        )
        try:
            cloud_cover = float(cloud_raw)
        except (TypeError, ValueError):
            cloud_cover = 100.0
        if cloud_cover > max_cloud_cover:
            continue

        scene: dict[str, Any] = {
            "id": str(item.get("id", "")),
            "datetime": properties.get("datetime"),
            "cloud_cover": cloud_cover,
            "platform": properties.get("platform", "Sentinel-2"),
            "proj_epsg": properties.get("proj:epsg"),
            "red_url": red_url,
            "nir_url": nir_url,
            "scl_url": scl_url,
            "green_url": green_url,
            "blue_url": blue_url,
        }
        boa_dn = properties.get(
            "s2:boa_add_offset", properties.get("boa_add_offset")
        )
        if isinstance(boa_dn, (int, float)):
            scene["boa_offset_dn"] = float(boa_dn)
        boa_applied = properties.get("earthsearch:boa_offset_applied")
        if isinstance(boa_applied, bool):
            scene["boa_offset_applied"] = boa_applied
        # Ключи коррекции добавляются только при наличии метаданных: отсутствие
        # scale/offset не должно превращаться в фиктивное значение.
        for key, value in (
            ("red_scale", red_scale),
            ("red_offset", red_offset),
            ("green_scale", green_scale),
            ("green_offset", green_offset),
            ("blue_scale", blue_scale),
            ("blue_offset", blue_offset),
            ("nir_scale", nir_scale),
            ("nir_offset", nir_offset),
            ("scl_scale", scl_scale),
            ("scl_offset", scl_offset),
        ):
            if value is not None:
                scene[key] = value
        scenes.append(scene)
        if len(scenes) >= limit:
            break
    return scenes


async def search_sentinel2_scenes(
    bbox: list[float],
    start_date: str,
    end_date: str,
    max_cloud_cover: int = 20,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ищет сцены Sentinel-2 L2A в Earth Search и возвращает COG-ссылки.

    Поиск выполняется в отдельном потоке (``asyncio.to_thread``), т.к.
    ``pystac-client`` синхронный. Сетевые ошибки повторяются с backoff и
    ограничены таймаутом, чтобы граф не зависал.

    Args:
        bbox: [minx, miny, maxx, maxy] в EPSG:4326.
        start_date: Начало периода, ``YYYY-MM-DD``.
        end_date: Конец периода, ``YYYY-MM-DD``.
        max_cloud_cover: Максимальная облачность, %.
        limit: Максимум сцен.

    Returns:
        Список словарей со сценами:
        ``id``, ``datetime``, ``cloud_cover``, ``platform``, ``proj_epsg``,
        ``red_url`` (B04), ``nir_url`` (B08), ``scl_url`` (SCL).
        Сцены без любого из трёх COG-ассетов пропускаются.
    """
    last_error: Exception | None = None
    for attempt in range(1, _STAC_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    _search_sentinel2_scenes_sync,
                    bbox,
                    start_date,
                    end_date,
                    max_cloud_cover,
                    limit,
                ),
                timeout=_STAC_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — сеть STAC не должна ронять граф
            last_error = exc
            logger.warning(
                "Sentinel-2 STAC search attempt %d/%d failed: %s",
                attempt,
                _STAC_ATTEMPTS,
                exc,
            )
            if attempt < _STAC_ATTEMPTS:
                await asyncio.sleep(_STAC_BACKOFF_SECONDS * attempt)
    logger.warning("Sentinel-2 STAC search failed after retries: %s", last_error)
    return []