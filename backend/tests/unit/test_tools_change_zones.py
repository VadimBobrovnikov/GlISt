"""Unit-тесты полигонализации зон изменений (T053)."""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import Affine

from glist.tools.change_zones import (
    build_change_zones,
    mask_to_zones,
    pixel_area_m2,
)

_TRANSFORM = Affine(10, 0, 500000, 0, -10, 6200000)
_CRS = "EPSG:32637"


def test_pixel_area_m2() -> None:
    assert pixel_area_m2(_TRANSFORM) == pytest.approx(100.0)


def test_mask_to_zones_filters_small_blobs() -> None:
    mask = np.zeros((10, 10), dtype=bool)
    mask[1:3, 1:3] = True  # 2x2 = 4 пикселя = 400 м²
    mask[7, 7] = True  # 1 пиксель = 100 м²

    zones = mask_to_zones(
        mask,
        _TRANSFORM,
        _CRS,
        "retained",
        "Москва",
        min_area_m2=400,
    )

    assert len(zones) == 1
    assert zones[0]["change_class"] == "retained"
    assert zones[0]["territory_name"] == "Москва"
    assert zones[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_build_change_zones_for_all_classes() -> None:
    retained = np.zeros((8, 8), dtype=bool)
    retained[0:3, 0:3] = True
    lost = np.zeros((8, 8), dtype=bool)
    lost[0:3, 4:7] = True
    new = np.zeros((8, 8), dtype=bool)
    new[5:7, 1:7] = True

    zones = build_change_zones(
        {"retained": retained, "lost": lost, "new": new},
        _TRANSFORM,
        _CRS,
        "Москва",
        min_area_m2=100,
    )

    assert {zone["change_class"] for zone in zones} == {
        "retained",
        "lost",
        "new",
    }


def test_mask_to_zones_rejects_unknown_class() -> None:
    mask = np.zeros((4, 4), dtype=bool)
    with pytest.raises(ValueError):
        mask_to_zones(mask, _TRANSFORM, _CRS, "unknown", "Москва")
