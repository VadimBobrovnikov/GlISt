"""GeoJSON export tool (T054).

Provides function to assemble GeoJSON FeatureCollection from
change-detection zones with proper CRS and attributes.
"""
from typing import Any


def export_geojson(
    zones: list[dict[str, Any]],
    territory_name: str = "Unknown",
    mode: str = "single",
) -> dict[str, Any]:
    """Export change zones as GeoJSON FeatureCollection.

    Creates a GeoJSON FeatureCollection matching the contract schema
    specs/001-geo-analysis-platform/contracts/geojson-export.schema.json.

    Feature properties include:
    - territory_name: Name from query text
    - change_class: "retained", "lost", or "new"
    - area_km2: Area in square kilometers (calculated from geometry)
    - period_start: Start date string
    - period_end: End date string
    - run_id: UUID string
    - result_id: UUID string

    Args:
        zones: List of zone dicts with keys:
            - geometry: GeoJSON geometry dict (Polygon or MultiPolygon)
            - change_class: One of "retained", "lost", "new"
            - area_km2: Float area in km2
            - period_start: ISO date string
            - period_end: ISO date string
            - run_id: UUID string
            - result_id: UUID string
        territory_name: Territory name from query (default: "Unknown")
        mode: "single" or "combined" (default: "single")

    Returns:
        GeoJSON FeatureCollection dict conforming to the JSON schema.
    """
    if not zones:
        # Return empty but valid FeatureCollection
        return {
            "type": "FeatureCollection",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": [],
        }

    features = []
    for i, zone in enumerate(zones):
        geometry = zone.get("geometry")
        change_class = zone.get("change_class", "retained")
        area_km2 = zone.get("area_km2", 0.0)
        period_start = zone.get("period_start", "")
        period_end = zone.get("period_end", "")
        run_id = zone.get("run_id", f"run-{i}")
        result_id = zone.get("result_id", f"result-{i}")

        # Validate change_class
        if change_class not in ("retained", "lost", "new"):
            change_class = "retained"

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "territory_name": territory_name,
                "change_class": change_class,
                "area_km2": area_km2,
                "period_start": period_start,
                "period_end": period_end,
                "run_id": run_id,
                "result_id": result_id,
            },
        }
        features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": features,
    }

    return geojson


def export_assemble_geojson(
    zones: list[dict[str, Any]],
    territory_name: str = "Unknown",
    mode: str = "single",
) -> dict[str, Any]:
    """Alias for export_geojson for test import compatibility.

    Args:
        zones: List of zone dicts.
        territory_name: Territory name.
        mode: Export mode.

    Returns:
        GeoJSON FeatureCollection dict.
    """
    return export_geojson(zones, territory_name, mode)


def calculate_feature_area(
    geometry: dict[str, Any],
    crs_from: str = "EPSG:4326",
    crs_to: str = "EPSG:3857",
) -> float:
    """Calculate area of a GeoJSON geometry in square kilometers.

    Note: This is a placeholder. Actual area calculation should use
    PostGIS ST_Area with proper projected CRS.

    Args:
        geometry: GeoJSON geometry dict (Polygon or MultiPolygon).
        crs_from: Source CRS (default: EPSG:4326).
        crs_to: Target projected CRS for area calculation (default: EPSG:3857).

    Returns:
        Area in square kilometers (approximate for WGS84).
    """
    # Placeholder: return approximate area based on bounding box
    # In production, use PostGIS: ST_Area(ST_Transform(geometry, 3857))
    if geometry.get("type") == "Polygon":
        coords = geometry.get("coordinates", [[]])
        if coords and len(coords) > 0:
            # Very rough approximation for bbox-based area
            bbox = coords[0]
            if len(bbox) >= 4:
                lon_width = abs(bbox[0][0] - bbox[-1][0])
                lat_height = abs(bbox[0][1] - bbox[-1][1])
                # Approximate km per degree at equator
                km_lon = float(lon_width) * 111.32
                km_lat = float(lat_height) * 110.57
                return km_lon * km_lat / 2  # rough estimate
        return 0.0
    elif geometry.get("type") == "MultiPolygon":
        coords = geometry.get("coordinates", [])
        # Sum areas of all polygons
        total_area = 0.0
        for polygon_coords in coords:
            if polygon_coords:
                area = calculate_feature_area(
                    {"type": "Polygon", "coordinates": polygon_coords},
                    crs_from,
                    crs_to,
                )
                total_area += area
        return total_area

    return 0.0