"""Unit-тесты Analyst: NDVI pipeline с моками (без сети и файлов)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from rasterio.transform import Affine

from glist.agents import analyst as analyst_module
from glist.agents.analyst import analyst_node

_TRANSFORM = Affine(10, 0, 500000, 0, -10, 6200000)
_SHAPE = (8, 8)

_START_SCENE = {
    "id": "S2-START",
    "red_url": "start-red",
    "nir_url": "start-nir",
    "scl_url": "start-scl",
    "proj_epsg": 32637,
    "cloud_cover": 5.0,
    "datetime": "2020-06-01T00:00:00Z",
}
_END_SCENE = {
    "id": "S2-END",
    "red_url": "end-red",
    "nir_url": "end-nir",
    "scl_url": "end-scl",
    "proj_epsg": 32637,
    "cloud_cover": 8.0,
    "datetime": "2023-06-01T00:00:00Z",
}


def _start_red() -> np.ndarray:
    array = np.full(_SHAPE, 0.2, dtype=np.float32)
    array[4:8, :] = 0.6  # зона C: старт — не-растительность
    return array


def _start_nir() -> np.ndarray:
    array = np.full(_SHAPE, 0.6, dtype=np.float32)
    array[4:8, :] = 0.2
    return array


def _end_red() -> np.ndarray:
    array = np.full(_SHAPE, 0.2, dtype=np.float32)
    array[0:4, 4:8] = 0.6  # зона B: конец — не-растительность
    return array


def _end_nir() -> np.ndarray:
    array = np.full(_SHAPE, 0.6, dtype=np.float32)
    array[0:4, 4:8] = 0.2
    return array


_SCL = np.full(_SHAPE, 4, dtype=np.uint8)


def _fake_read(
    url: str,
    bbox: tuple[float, float, float, float],
    band_number: int = 1,
    scale: float = 1.0,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, Affine]:
    arrays = {
        "start-red": _start_red(),
        "start-nir": _start_nir(),
        "start-scl": _SCL,
        "end-red": _end_red(),
        "end-nir": _end_nir(),
        "end-scl": _SCL,
    }
    return arrays[url], _TRANSFORM


def _state(name: str = "Москва") -> dict[str, Any]:
    return {
        "context": {
            "territories": [{"name": name}],
            "period": {"start": "2020-06-01", "end": "2023-06-01"},
            "ndvi_threshold": 0.25,
            "min_area_m2": 100,
        }
    }


@pytest.mark.asyncio
async def test_analyst_builds_real_change_zones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    with (
        patch.object(
            analyst_module,
            "geocode_territory",
            AsyncMock(return_value={"bbox": [37.5, 55.7, 37.6, 55.8]}),
        ),
        patch.object(
            analyst_module,
            "search_sentinel2_scenes",
            AsyncMock(side_effect=[[_START_SCENE], [_END_SCENE]]),
        ),
        patch.object(analyst_module, "read_cog_window", side_effect=_fake_read),
    ):
        update = await analyst_node(_state())

    context = update["context"]
    zones = context["change_zones"]
    assert update["next_agent"] == "visualizer"
    assert {zone["change_class"] for zone in zones} == {
        "retained",
        "lost",
        "new",
    }
    assert all(
        zone["geometry"]["type"] in ("Polygon", "MultiPolygon")
        for zone in zones
    )
    assert context["datasets"]["accepted"]
    assert context["analysis_type"] == "vegetation_change"
    diagnostics = context["analysis_diagnostics"]
    assert diagnostics["coverage"]["sharedValid"] == pytest.approx(1.0)
    assert diagnostics["sceneOverlap"]["startEndDisjoint"] is True
    assert diagnostics["classification"]["ndviThreshold"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_analyst_reports_no_suitable_scenes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    with (
        patch.object(
            analyst_module,
            "geocode_territory",
            AsyncMock(return_value={"bbox": [37.5, 55.7, 37.6, 55.8]}),
        ),
        patch.object(
            analyst_module,
            "search_sentinel2_scenes",
            AsyncMock(side_effect=[[], [], [], []]),
        ),
    ):
        update = await analyst_node(_state())

    assert update["next_agent"] == "validator"
    assert update["context"]["change_zones"] == []
    assert "Нет пригодных сцен" in update["context"]["analysis_error"]


@pytest.mark.asyncio
async def test_analyst_reports_unresolved_territory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    with patch.object(
        analyst_module,
        "geocode_territory",
        AsyncMock(return_value=None),
    ):
        update = await analyst_node(_state("Атлантида"))

    assert update["next_agent"] == "validator"
    assert update["context"]["change_zones"] == []
    assert "Не удалось определить границы" in update["context"]["analysis_error"]
