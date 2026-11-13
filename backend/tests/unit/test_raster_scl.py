"""Unit-тесты SCL-маски, NaN-композита и ресемплинга COG-окна."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.windows import Window

from glist.tools.raster_ops import mask_scl, median_composite, read_cog_window


def test_mask_scl_default_classes() -> None:
    scl = np.array([[4, 8], [0, 5]], dtype=np.uint8)
    mask = mask_scl(scl)
    np.testing.assert_array_equal(mask, np.array([[False, True], [True, False]]))


def test_mask_scl_custom_classes() -> None:
    scl = np.array([[4, 8]], dtype=np.uint8)
    mask = mask_scl(scl, {4})
    np.testing.assert_array_equal(mask, np.array([[True, False]]))


def test_median_composite_ignores_nan() -> None:
    first = np.array([[1.0, np.nan], [3.0, 4.0]], dtype=np.float32)
    second = np.array([[5.0, 2.0], [np.nan, 8.0]], dtype=np.float32)
    result = median_composite([first, second])
    np.testing.assert_allclose(
        result,
        np.array([[3.0, 2.0], [3.0, 6.0]], dtype=np.float32),
    )


def test_read_cog_window_with_out_shape() -> None:
    dataset = MagicMock()
    dataset.crs = None
    dataset.transform = Affine.identity()
    dataset.read.return_value = np.ones((2, 3), dtype=np.float32)
    dataset.window_transform.return_value = Affine.identity()
    open_mock = MagicMock()
    open_mock.return_value.__enter__.return_value = dataset
    window = Window(col_off=0, row_off=0, width=6, height=4)

    with (
        patch("glist.tools.raster_ops.rasterio.open", open_mock),
        patch("glist.tools.raster_ops.from_bounds", return_value=window),
    ):
        data, transform = read_cog_window(
            "https://example.com/B04.tif",
            (0.0, 0.0, 1.0, 1.0),
            out_shape=(2, 3),
            resampling=Resampling.nearest,
        )

    dataset.read.assert_called_once_with(
        1,
        window=window,
        out_shape=(2, 3),
        resampling=Resampling.nearest,
    )
    assert data.shape == (2, 3)
    assert isinstance(transform, Affine)
