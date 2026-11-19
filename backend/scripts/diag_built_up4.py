"""SCL-распределение по конкретным сценам (поиск источника NoData)."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

from glist.agents.analyst import _read_band_window
from glist.tools.stac_search import search_sentinel2_scenes

BBOX = [48.8206, 55.6031, 49.3812, 55.9307]


async def main() -> None:
    scenes = await search_sentinel2_scenes(
        BBOX, "2026-10-05", "2027-01-03", max_cloud_cover=1, limit=12
    )
    print("scenes", [s.get("id") for s in scenes], flush=True)
    for scene in scenes[:4]:
        with rasterio.open(scene["scl_url"]) as src:
            bounds = [round(v) for v in src.bounds]
        data, _ = _read_band_window(scene["scl_url"], tuple(BBOX), 1.0, None, 0.0)
        classes, counts = np.unique(np.rint(data).astype("int16"), return_counts=True)
        total = data.size
        share = {
            int(c): round(float(n) / total * 100, 2)
            for c, n in zip(classes, counts, strict=False)
        }
        print(scene.get("id"), "bounds", bounds, "scl", json.dumps(share), flush=True)


asyncio.run(main())
