"""Tests for landcover classification tool (T045).

Verifies that tools/landcover performs:
- Binary vegetation classification by NDVI threshold
- Three change detection classes: retained, lost, new

Tests are written in TDD style: on first run (no module), they should
fail with ImportError, then pass after tools/landcover.py is created.
"""
import numpy as np
import pytest


class TestToolsLandcover:
    """Landcover classification tool tests - TDD style.

    These tests are expected to FAIL on first run (ImportError)
    because tools/landcover.py does not exist yet.
    After implementation, they should PASS.
    """

    def test_landcover_binary_classification(self):
        """Landcover should classify vegetation by NDVI threshold (e.g., > 0.2)."""
        from glist.tools.landcover import classify_vegetation
        # TODO: implement test after tool creation
        pass

    def test_landcover_three_change_classes(self):
        """Landcover should produce three classes: retained, lost, new."""
        from glist.tools.landcover import detect_changes
        # TODO: implement test after tool creation
        pass