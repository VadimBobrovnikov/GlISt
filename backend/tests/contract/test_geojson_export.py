"""Contract tests for GeoJSON export (T042).

Verifies that GeoJSON export output conforms to the JSON schema
specs/001-geo-analysis-platform/contracts/geojson-export.schema.json.

Checks structure, CRS (EPSG:4326), and required properties:
territory_name, change_class, area_km2.
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMA_PATH = PROJECT_ROOT / "specs" / "001-geo-analysis-platform" / "contracts" / "geojson-export.schema.json"


def load_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


MINIMAL_GC = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
    "properties": {
        "runId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "resultId": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "generatedAt": "2024-01-15T10:30:00Z",
        "mode": "single",
        "territoriesCount": 2,
        "hasOverlap": False,
        "aggregateNote": "сумма по территориям",
        "sourcesCount": 1,
    },
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[30.0, 50.0], [31.0, 50.0], [31.0, 51.0], [30.0, 51.0], [30.0, 50.0]]],
            },
            "properties": {
                "territory_name": "Мoscow",
                "change_class": "retained",
                "area_km2": 1.5,
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "result_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            },
        }
    ],
}


class TestGeoJSONExportContract:
    """Contract tests for GeoJSON export output."""

    def test_geojson_has_required_top_level_keys(self):
        """FeatureCollection must have required top-level keys: type, crs, properties, features."""
        schema = load_schema()
        data = MINIMAL_GC
        required_top = schema.get("required", [])
        for key in required_top:
            assert key in data, f"Missing required top-level key: {key}"

    def test_geojson_crs_is_epsg4326(self):
        """CRS must be defined with EPSG:4326 (WGS 84) name identifier."""
        schema = load_schema()
        data = MINIMAL_GC
        crs = data.get("crs", {})
        assert crs.get("type") == "name"
        assert crs.get("properties", {}).get("name") == "urn:ogc:def:crs:OGC:1.3:CRS84"

    def test_feature_properties_required_fields(self):
        """Each feature must have required properties: territory_name, change_class, area_km2."""
        data = MINIMAL_GC
        schema = load_schema()
        features = data.get("features", [])
        change_zone = schema.get("$defs", {}).get("changeZoneFeature", {})
        prop_req = change_zone.get("properties", {}).get("required", [])
        for i, feature in enumerate(features):
            props = feature.get("properties", {})
            for key in prop_req:
                assert key in props, f"Feature {i}: missing required property '{key}'"
            change_class = props.get("change_class")
            assert change_class in {"retained", "lost", "new"}, f"Invalid change_class: {change_class}"
            area = props.get("area_km2")
            assert isinstance(area, (int, float)) and area >= 0, f"Invalid area_km2: {area}"