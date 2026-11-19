"""Проверка компонентов валидной маски на лучшей сцене."""

from __future__ import annotations

import asyncio

import numpy as np

from glist.agents.analyst import _band_scaling, _read_band_window
from glist.scenarios.built_up import _VALID_SCL
from glist.tools.stac_search import search_sentinel2_scenes

BBOX = [48.8206, 55.6031, 49.3812, 55.9307]


async def main() -> None:
    scenes = await search_sentinel2_scenes(
        BBOX, "2026-10-05", "2027-01-03", max_cloud_cover=1, limit=12
    )
    scene = next(s for s in scenes if s.get("id") == "S2B_39UUB_20261030_0_L2A")
    bbox = tuple(BBOX)
    swir_scale, swir_offset, _ = _band_scaling(scene, "swir16", 0.0001)
    nir_scale, nir_offset, _ = _band_scaling(scene, "nir", 0.0001)
    red_scale, red_offset, _ = _band_scaling(scene, "red", 0.0001)
    swir, transform = _read_band_window(
        scene["swir16_url"], bbox, swir_scale, None, swir_offset
    )
    shape = swir.shape
    nir, _ = _read_band_window(
        scene["nir_url"], bbox, nir_scale, shape, nir_offset
    )
    red, _ = _read_band_window(
        scene["red_url"], bbox, red_scale, shape, red_offset
    )
    scl, _ = _read_band_window(scene["scl_url"], bbox, 1.0, shape, 0.0)
    total = swir.size
    scl_ok = np.isin(np.rint(scl).astype("int16"), _VALID_SCL)
    print("shape", shape, "total", total, flush=True)
    for name, cond in (
        ("scl_ok", scl_ok),
        ("swir>0", swir > 0),
        ("nir>0", nir > 0),
        ("red>0", red > 0),
        ("all", scl_ok & (swir > 0) & (nir > 0) & (red > 0)),
    ):
        print(name, round(float(cond.mean()) * 100, 2), flush=True)
    print(
        "nir p5/p50/p95",
        [round(float(np.percentile(nir, p)), 4) for p in (5, 50, 95)],
        flush=True,
    )
    print(
        "swir p5/p50/p95",
        [round(float(np.percentile(swir, p)), 4) for p in (5, 50, 95)],
        flush=True,
    )
    print(
        "red p5/p50/p95",
        [round(float(np.percentile(red, p)), 4) for p in (5, 50, 95)],
        flush=True,
    )


asyncio.run(main())
