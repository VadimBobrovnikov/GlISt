"""Unit-тесты коррекции scale/offset и площади сетки (без сети)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from rasterio.transform import Affine
from rasterio.windows import Window

from glist.tools.raster_ops import (
    grid_pixel_area_m2,
    read_cog_window,
    resolve_band_scaling,
)


def test_resolve_scaling_prefers_dataset_values_once() -> None:
    scale, offset, source = resolve_band_scaling(0.0001, -0.1, 0.0001, 0.0)
    assert (scale, offset, source) == (0.0001, -0.1, "dataset")


def test_resolve_scaling_uses_requested_when_dataset_has_no_correction() -> None:
    scale, offset, source = resolve_band_scaling(1.0, 0.0, 0.0001, -0.1)
    assert (scale, offset, source) == (0.0001, -0.1, "requested")


def test_read_cog_window_applies_stac_offset_in_reflectance_units() -> None:
    dataset = MagicMock()
    dataset.crs = None
    dataset.transform = "RASTER_TRANSFORM"
    dataset.window_transform.return_value = "WINDOW_TRANSFORM"
    # Реальные метаданные Earth Search для сцены 2023: scale=0.0001, offset=-0.1.
    dataset.scales = (0.0001,)
    dataset.offsets = (-0.1,)
    dataset.read.return_value = np.array([[2000]], dtype=np.uint16)
    open_mock = MagicMock()
    open_mock.return_value.__enter__.return_value = dataset

    with (
        patch("glist.tools.raster_ops.rasterio.open", open_mock),
        patch(
            "glist.tools.raster_ops.from_bounds",
            return_value=Window(0, 0, 1, 1),
        ),
    ):
        data, _ = read_cog_window(
            "https://example.com/B04.tif",
            (0.0, 0.0, 1.0, 1.0),
            scale=0.0001,
            offset=-0.1,
        )

    assert data.dtype == np.float32
    # 2000 * 0.0001 + (-0.1) = 0.1; не (2000 - 0.1) * 0.0001.
    assert data[0, 0] == pytest.approx(0.1)


def test_read_cog_window_reads_full_window_for_real_dataset() -> None:
    dataset = MagicMock()
    dataset.crs = None
    dataset.transform = "RASTER_TRANSFORM"
    dataset.window_transform.return_value = "WINDOW_TRANSFORM"
    dataset.width = 1000
    dataset.height = 1000
    dataset.read.return_value = np.zeros((4, 5), dtype=np.uint16)
    open_mock = MagicMock()
    open_mock.return_value.__enter__.return_value = dataset

    with (
        patch("glist.tools.raster_ops.rasterio.open", open_mock),
        patch(
            "glist.tools.raster_ops.from_bounds",
            return_value=Window(-5, 2, 5, 4),
        ),
    ):
        read_cog_window(
            "https://example.com/B04.tif",
            (0.0, 0.0, 1.0, 1.0),
            scale=0.0001,
        )

    kwargs = dataset.read.call_args.kwargs
    assert kwargs.get("boundless") is True
    assert kwargs.get("fill_value") == 0


def test_analyst_band_scaling_prefers_stac_units_and_converts_boa_dn() -> None:
    from glist.agents.analyst import _band_scaling

    stac_scene = {"red_scale": 0.0001, "red_offset": -0.1, "boa_offset_dn": -1000.0}
    assert _band_scaling(stac_scene, "red", 0.0001) == (0.0001, -0.1, "stac")

    boa_scene = {"boa_offset_dn": -1000.0}
    scale, offset, source = _band_scaling(boa_scene, "red", 0.0001)
    assert (scale, source) == (0.0001, "boa_converted")
    assert offset == pytest.approx(-0.1)

    plain = _band_scaling({}, "red", 0.0001)
    assert plain == (0.0001, 0.0, "default")


def test_pixel_area_projected_and_geographic() -> None:
    projected = grid_pixel_area_m2(Affine(10, 0, 500000, 0, -10, 6200000), "EPSG:32637", 55.7)
    assert projected == pytest.approx(100.0)
    geographic = grid_pixel_area_m2(
        Affine(0.0001, 0, 37.5, 0, -0.0001, 55.8),
        "EPSG:4326",
        55.8,
    )
    assert geographic is not None
    assert 40 < geographic < 80
    assert grid_pixel_area_m2(Affine(10, 0, 0, 0, -10, 0), None, 0.0) is None
