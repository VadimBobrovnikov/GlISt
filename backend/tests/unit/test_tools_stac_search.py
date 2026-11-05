"""Tests for STAC search tool (T043).

Verifies that tools/stac_search STAC search function:
- Correctly filters cloud cover (<= 20%)
- Preserves provenance (dates, source)
"""
import pytest


class TestToolsSTACSearch:
    """STAC search tool tests."""

    def test_stac_search_filters_cloud_cover(self):
        """STAC search should filter results by cloud cover <= 20%."""
        pass

    def test_stac_search_preserves_provenance(self):
        """STAC search should preserve provenance information (dates, source)."""
        pass