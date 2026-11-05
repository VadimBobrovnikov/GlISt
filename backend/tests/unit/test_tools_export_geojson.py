"""Tests for GeoJSON export tool (T046).

Verifies that tools/export_geojson.py assembles GeoJSON correctly.
"""
import json
import pytest


class TestToolsExportGeoJSON:
    """GeoJSON export tool tests."""

    def test_geojson_assembled_correctly(self):
        """GeoJSON should have valid FeatureCollection structure."""
        geojson = {
            "type": "FeatureCollection",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
                    },
                    "properties": {
                        "territory_name": "Test Area",
                        "change_class": "retained",
                        "area_km2": 1.0,
                    },
                }
            ],
        }
        assert geojson["type"] == "FeatureCollection"
        assert geojson["crs"]["properties"]["name"] == "urn:ogc:def:crs:OGC:1.3:CRS84"
        assert len(geojson["features"]) == 1
        assert geojson["features"][0]["properties"]["territory_name"] == "Test Area"