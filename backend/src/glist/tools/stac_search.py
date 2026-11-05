"""STAC search tool for Sentinel-2 and Landsat imagery (T049).

Provides functions to search for satellite imagery via STAC API
using pystac-client, with cloud cover filtering and metadata extraction.
"""
from typing import Any

import pystac
import pystac_client


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