"""Unit-тесты качества данных, сетки и валидатора (без сети)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from rasterio.transform import Affine

from glist.agents import analyst as analyst_module
from glist.agents.analyst import analyst_node
from glist.agents.validator import validator_node

_SHAPE = (8, 8)
_START_TRANSFORM = Affine(10, 0, 500000, 0, -10, 6200000)
_END_TRANSFORM_SHIFTED = Affine(10, 0, 500010, 0, -10, 6200000)

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


def _state() -> dict[str, Any]:
    return {
        "context": {
            "territories": [{"name": "Москва"}],
            "period": {"start": "2020-06-01", "end": "2023-06-01"},
            "ndvi_threshold": 0.25,
            "min_area_m2": 1,
        }
    }


def _grid(crs: str = "EPSG:32637", origin: float = 500000.0) -> dict[str, Any]:
    return analyst_module._grid_public(
        Affine(10, 0, origin, 0, -10, 6200000),
        crs,
        _SHAPE,
    )


def test_grid_differences_detects_shift_and_resolution() -> None:
    same = analyst_module._grid_differences(_grid(), _grid())
    assert same["compatibleWithoutResampling"] is True
    assert same["originShiftPixels"] == pytest.approx(0.0)

    shifted = analyst_module._grid_differences(_grid(), _grid(origin=500010.0))
    assert shifted["compatibleWithoutResampling"] is False
    assert shifted["originShiftPixels"] == pytest.approx(1.0)

    resampled = analyst_module._grid_differences(
        _grid(),
        {
            **_grid(),
            "resolution": [20.0, 20.0],
        },
    )
    assert resampled["resolutionEqual"] is False

    other_crs = analyst_module._grid_differences(_grid(), _grid("EPSG:32638"))
    assert other_crs["crsEqual"] is False


def _fake_read(
    url: str,
    bbox: tuple[float, float, float, float],
    band_number: int = 1,
    scale: float = 1.0,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, Affine]:
    """Данные сцены; старт наполовину облачный, конец полностью валиден."""
    if url == "start-scl":
        scl = np.full(_SHAPE, 4, dtype=np.uint8)
        scl[:, :4] = 8
        return scl, _START_TRANSFORM
    if url == "end-scl":
        return np.full(_SHAPE, 4, dtype=np.uint8), _END_TRANSFORM_SHIFTED
    if url.startswith("start"):
        return np.full(_SHAPE, 0.2 if url == "start-red" else 0.6, np.float32), (
            _START_TRANSFORM
        )
    if url.startswith("end"):
        return np.full(_SHAPE, 0.2 if url == "end-red" else 0.6, np.float32), (
            _END_TRANSFORM_SHIFTED
        )
    raise AssertionError(url)


@pytest.mark.asyncio
async def test_period_composite_rejects_scene_with_other_crs() -> None:
    first = {
        "id": "A",
        "red_url": "a-red",
        "nir_url": "a-nir",
        "scl_url": "a-scl",
        "proj_epsg": 32637,
        "datetime": "2020-06-01T00:00:00Z",
    }
    second = {**first, "id": "B", "proj_epsg": 32638}

    def fake(
        url: str,
        bbox: tuple[float, float, float, float],
        band_number: int = 1,
        scale: float = 1.0,
        out_shape: tuple[int, int] | None = None,
    ) -> tuple[np.ndarray, Affine]:
        if url.endswith("scl"):
            return np.full(_SHAPE, 4, dtype=np.uint8), _START_TRANSFORM
        return np.full(_SHAPE, 0.2, dtype=np.float32), _START_TRANSFORM

    with patch.object(analyst_module, "read_cog_window", side_effect=fake):
        composite, grid, accepted, rejected = await analyst_module._period_composite(
            [first, second], [37.5, 55.7, 37.6, 55.8]
        )

    assert composite is not None
    assert grid is not None
    assert [item["sceneId"] for item in accepted] == ["A"]
    assert {item["reason"] for item in rejected} == {"grid_crs_mismatch"}


@pytest.mark.asyncio
async def test_analyst_marks_low_coverage_as_limited_not_passed(
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
    diagnostics = context["analysis_diagnostics"]
    assert context["analysis_sufficient"] is False
    assert diagnostics["technicalSuccess"] is True
    assert diagnostics["interpretability"]["status"] == "limited"
    assert diagnostics["coverage"]["sharedValid"] == pytest.approx(0.5)
    assert diagnostics["unknown"]["share"] == pytest.approx(0.5)
    assert diagnostics["coverage"]["sharedValidAreaKm2"] is not None
    assert diagnostics["grid"]["alignment"] == "end_reprojected_to_start"
    assert diagnostics["grid"]["check"]["originShiftPixels"] == pytest.approx(1.0)
    assert diagnostics["periodSelection"]["method"] == "requested"
    assert any(
        "валидное покрытие" in item.lower() for item in diagnostics["limitations"]
    )

    validation = await validator_node(update)
    assert validation["validation"]["outcome"] == "failed"
    assert validation["validation"]["category"] == "data_quality"
    assert validation["validation"]["reasons"]


@pytest.mark.asyncio
async def test_analyst_missing_period_is_explicit_technical_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    state = {
        "context": {
            "territories": [{"name": "Москва"}],
            "period": {"start": "не дата", "end": "2023-01-01"},
        }
    }
    update = await analyst_node(state)
    context = update["context"]
    assert context["analysis_failure"]["category"] == "analysis_error"
    assert context["analysis_failure"]["stage"] == "input_validation"

    validation = await validator_node(update)
    assert validation["validation"]["outcome"] == "failed"
    assert validation["validation"]["category"] == "analysis_error"


@pytest.mark.asyncio
async def test_analyst_read_failure_is_technical_not_data_quality(
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
        patch.object(
            analyst_module,
            "read_cog_window",
            side_effect=RuntimeError("technical read failure"),
        ),
    ):
        update = await analyst_node(_state())

    context = update["context"]
    diagnostics = context["analysis_diagnostics"]
    assert context["analysis_failure"]["category"] == "analysis_error"
    assert context["analysis_failure"]["stage"] == "scene_read"
    assert diagnostics["technicalSuccess"] is False
    assert diagnostics["interpretability"]["status"] == "not_evaluated"

    validation = await validator_node(update)
    assert validation["validation"]["outcome"] == "failed"
    assert validation["validation"]["category"] == "analysis_error"
    assert validation["validation"]["reasons"]


@pytest.mark.asyncio
async def test_chunk_bboxes_cover_full_bbox_without_gaps() -> None:
    bbox = [37.0, 55.0, 37.56, 55.33]
    chunks = analyst_module._chunk_bboxes(bbox, 0.25)
    assert len(chunks) == 6
    assert min(chunk[0] for chunk in chunks) == pytest.approx(37.0)
    assert max(chunk[2] for chunk in chunks) == pytest.approx(37.56)
    assert min(chunk[1] for chunk in chunks) == pytest.approx(55.0)
    assert max(chunk[3] for chunk in chunks) == pytest.approx(55.33)


def test_aoi_mask_fills_bbox_polygon() -> None:
    geometry = analyst_module._bbox_polygon([0.0, 0.0, 1.0, 1.0])
    mask = analyst_module._aoi_mask(
        geometry,
        Affine(0.1, 0, 0.0, 0, -0.1, 1.0),
        (10, 10),
        "EPSG:4326",
    )
    assert mask.shape == (10, 10)
    assert bool(mask.all())


def test_aoi_mask_reprojects_to_raster_crs() -> None:
    geometry = analyst_module._bbox_polygon([37.5, 55.7, 37.6, 55.8])
    mask = analyst_module._aoi_mask(
        geometry,
        Affine(10, 0, 420000, 0, -10, 6200000),
        (8, 8),
        "EPSG:32637",
    )
    assert mask.shape == (8, 8)
    assert mask.dtype == bool


@pytest.mark.asyncio
async def test_district_without_parent_is_not_replaced_by_city() -> None:
    with patch.object(
        analyst_module,
        "geocode_territory",
        AsyncMock(return_value=None),
    ) as geocode:
        aoi = await analyst_module._resolve_territory_aoi(
            {"name": "Восточный", "type": "district"},
            "Восточный",
        )
    assert aoi["bbox"] is None
    assert "не указан город" in str(aoi["error"])
    geocode.assert_not_awaited()


@pytest.mark.asyncio
async def test_district_not_found_does_not_fall_back_to_city() -> None:
    with patch.object(
        analyst_module,
        "geocode_territory",
        AsyncMock(return_value=None),
    ):
        aoi = await analyst_module._resolve_territory_aoi(
            {"name": "Восточный", "parent": "Улан-Удэ", "type": "district"},
            "Восточный",
        )
    assert aoi["bbox"] is None
    assert aoi["source"] == "district_not_found"
    assert "Улан-Удэ" in str(aoi["error"])


@pytest.mark.asyncio
async def test_district_aoi_uses_district_geometry_not_city() -> None:
    district_bbox = [107.7318, 51.8551, 107.76, 51.8763]
    payload = {
        "name": "Восточный",
        "displayName": "Восточный, Железнодорожный район, Улан-Удэ, Россия",
        "addresstype": "suburb",
        "bbox": district_bbox,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [107.7318, 51.8551],
                    [107.76, 51.8551],
                    [107.76, 51.8763],
                    [107.7318, 51.8763],
                    [107.7318, 51.8551],
                ]
            ],
        },
        "ambiguous": False,
        "candidates": [],
    }
    with patch.object(
        analyst_module,
        "geocode_territory",
        AsyncMock(return_value=payload),
    ):
        aoi = await analyst_module._resolve_territory_aoi(
            {"name": "Восточный", "parent": "Улан-Удэ", "type": "district"},
            "Восточный",
        )
    assert aoi["bbox"] == district_bbox
    assert aoi["bbox"] != [107.4083748, 51.7102432, 107.9584077, 52.0276612]
    assert aoi["source"] == "geocoding+polygon"


@pytest.mark.asyncio
async def test_analyst_district_failure_has_no_city_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    state = {
        "context": {
            "territories": [
                {
                    "name": "Восточный",
                    "parent": "Улан-Удэ",
                    "type": "district",
                }
            ],
            "period": {"start": "2025-01-01", "end": "2026-01-01"},
        }
    }
    with patch.object(
        analyst_module,
        "geocode_territory",
        AsyncMock(return_value=None),
    ):
        update = await analyst_node(state)
    context = update["context"]
    assert context["change_zones"] == []
    assert context["analysis_failure"]["stage"] == "territory_selection"
    assert context["analysis_diagnostics"]["requestedTerritory"]["parent"] == "Улан-Удэ"


@pytest.mark.asyncio
async def test_validator_requires_quality_for_interpretation() -> None:
    state = {
        "context": {
            "change_zones": [
                {
                    "change_class": "lost",
                    "area_km2": 0.1,
                    "geometry": {"type": "Polygon", "coordinates": []},
                }
            ],
            "analysis_sufficient": False,
            "analysis_diagnostics": {
                "limitations": ["Общее валидное покрытие 42.0% ниже порога."]
            },
        },
        "attempt_count": 1,
    }
    result = await validator_node(state)
    assert result["validation"]["outcome"] == "failed"
    assert result["validation"]["category"] == "data_quality"
    assert result["validation"]["reasons"] == [
        "Общее валидное покрытие 42.0% ниже порога."
    ]
