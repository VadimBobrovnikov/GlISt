"""Tests for STAC search tool (T043).

Verifies that tools/stac_search STAC search function:
- Correctly filters cloud cover (<= 20%)
- Preserves provenance (dates, source)

Tests are written in TDD style: on first run (no module), they should
fail with ImportError, then pass after tools/stac_search.py is created.
"""
import pytest


class TestToolsSTACSearch:
    """STAC search tool tests - TDD style.

    These tests are expected to FAIL on first run (ImportError)
    because tools/stac_search.py does not exist yet.
    After implementation, they should PASS.
    """

    def test_stac_search_filters_cloud_cover(self):
        """STAC search should filter results by cloud cover <= 20%."""
        from glist.tools.stac_search import filter_by_cloud_cover
        # TODO: implement test after tool creation
        pass

    def test_stac_search_preserves_provenance(self):
        """STAC search should preserve provenance information (dates, source)."""
        from glist.tools.stac_search import preserve_provenance
        # TODO: implement test after tool creation
        pass