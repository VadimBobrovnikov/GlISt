"""Unit-тесты композитов preview/sample и слоя unknown (без сети)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from glist.api import previews as previews_module

_SCENES = [{"redUrl": "red", "nirUrl": "nir", "sclUrl": "scl"}]


def _scl(shape: tuple[int, int] = (4, 4)) -> np.ndarray:
    return np.full(shape, 4, dtype=np.uint8)


@pytest.mark.asyncio
async def test_period_ndvi_composite_uses_scene_masks() -> None:
    red = np.full((4, 4), 0.2, dtype=np.float32)
    nir = np.full((4, 4), 0.6, dtype=np.float32)
    scl = _scl()
    scl[:, :2] = 8

    async def fake_read(
        url: str,
        bbox: Any,
        scale: float,
        out_shape: Any,
        semaphore: Any,
        dn_offset: float = 0.0,
    ) -> np.ndarray | None:
        return {"red": red, "nir": nir, "scl": scl}.get(url)

    with patch.object(previews_module, "_read_band", fake_read):
        ndvi, valid = await previews_module._period_ndvi_composite(
            _SCENES, (0.0, 0.0, 1.0, 1.0)
        )

    assert ndvi == pytest.approx(0.5)
    assert valid == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_unknown_rgba_marks_pixels_invalid_on_one_date() -> None:
    start = [{"sclUrl": "start-scl"}]
    end = [{"sclUrl": "end-scl"}]
    start_scl = _scl()
    end_scl = _scl()
    start_scl[0, 0] = 0

    async def fake_read(
        url: str,
        bbox: Any,
        scale: float,
        out_shape: Any,
        semaphore: Any,
        dn_offset: float = 0.0,
    ) -> np.ndarray | None:
        return start_scl if url == "start-scl" else end_scl

    with patch.object(previews_module, "_read_band", fake_read):
        rgba = await previews_module._unknown_rgba(
            start, end, (0.0, 0.0, 1.0, 1.0)
        )

    assert rgba is not None
    assert rgba.shape == (4, 4, 4)
    assert rgba[0, 0, 3] == 160
    assert rgba[1, 1, 3] == 0


@pytest.mark.asyncio
async def test_period_valid_mask_requires_at_least_one_valid_scene() -> None:
    cloudy = _scl()
    cloudy[:] = 9
    clear = _scl()

    async def fake_read(
        url: str,
        bbox: Any,
        scale: float,
        out_shape: Any,
        semaphore: Any,
        dn_offset: float = 0.0,
    ) -> np.ndarray | None:
        return cloudy if url == "cloudy" else clear

    with patch.object(previews_module, "_read_band", fake_read):
        mask = await previews_module._period_valid_mask(
            [{"sclUrl": "cloudy"}, {"sclUrl": "clear"}],
            (0.0, 0.0, 1.0, 1.0),
        )

    assert mask is not None
    assert bool(mask.all())


@pytest.mark.asyncio
async def test_period_ndvi_composite_returns_none_without_scenes() -> None:
    with patch.object(
        previews_module,
        "_read_band",
        AsyncMock(return_value=None),
    ):
        ndvi, valid = await previews_module._period_ndvi_composite(
            _SCENES, (0.0, 0.0, 1.0, 1.0)
        )
    assert ndvi is None
    assert valid == 0.0
