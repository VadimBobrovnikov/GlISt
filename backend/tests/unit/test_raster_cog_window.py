"""Unit-тесты read_cog_window (rasterio замокан, без сети и файлов)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.windows import Window

from glist.tools.raster_ops import read_cog_window


def _mock_dataset(read_result: np.ndarray) -> MagicMock:
    dataset = MagicMock()
    dataset.transform = "RASTER_TRANSFORM"
    dataset.read.return_value = read_result
    dataset.window_transform.return_value = "WINDOW_TRANSFORM"
    return dataset


def test_read_cog_window_reads_only_bbox_window_and_scales() -> None:
    dataset = _mock_dataset(np.full((4, 5), 2, dtype=np.float32))
    open_mock = MagicMock()
    open_mock.return_value.__enter__.return_value = dataset
    window = Window(col_off=1, row_off=2, width=3, height=4)

    with (
        patch("glist.tools.raster_ops.rasterio.open", open_mock),
        patch(
            "glist.tools.raster_ops.from_bounds",
            return_value=window,
        ) as from_bounds_mock,
    ):
        data, transform = read_cog_window(
            "https://example.com/B04.tif",
            (37.5, 55.7, 37.7, 55.9),
            band_number=1,
            scale=0.0001,
        )

    from_bounds_mock.assert_called_once_with(
        37.5,
        55.7,
        37.7,
        55.9,
        transform="RASTER_TRANSFORM",
    )
    dataset.read.assert_called_once_with(1, window=window)
    dataset.window_transform.assert_called_once_with(window)
    assert transform == "WINDOW_TRANSFORM"
    np.testing.assert_allclose(
        data,
        np.full((4, 5), 0.0002, dtype=np.float32),
    )


def test_read_cog_window_defaults_to_first_band_and_no_scale() -> None:
    dataset = _mock_dataset(np.array([[10, 20]], dtype=np.uint16))
    open_mock = MagicMock()
    open_mock.return_value.__enter__.return_value = dataset
    window = Window(col_off=0, row_off=0, width=2, height=1)

    with (
        patch("glist.tools.raster_ops.rasterio.open", open_mock),
        patch(
            "glist.tools.raster_ops.from_bounds",
            return_value=window,
        ),
    ):
        data, _transform = read_cog_window(
            "https://example.com/SCL.tif",
            (0.0, 0.0, 1.0, 1.0),
        )

    dataset.read.assert_called_once_with(1, window=window)
    np.testing.assert_array_equal(data, np.array([[10, 20]], dtype=np.uint16))
