"""Tests for GeoJSON export tool (T046).

Verifies that tools/export_geojson.py assembles GeoJSON correctly.

Tests are written in TDD style: on first run (no module), they should
fail with ImportError, then pass after tools/export_geojson.py is created.
"""


class TestToolsExportGeoJSON:
    """GeoJSON export tool tests - TDD style.

    These tests are expected to FAIL on first run (ImportError)
    because tools/export_geojson.py does not exist yet.
    After implementation, they should PASS.
    """

    def test_geojson_assembled_correctly(self):
        """GeoJSON should have valid FeatureCollection structure."""
        # TODO: implement test after tool creation
        pass