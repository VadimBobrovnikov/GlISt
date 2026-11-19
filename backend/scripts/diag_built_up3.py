"""Проверка окна чтения COG: границы растра vs запрошенный bbox."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

from glist.agents.analyst import _band_scaling, _read_band_window, _window
from glist.tools.stac_search import search_sentinel2_scenes

BBOX = [48.8206, 55.6031, 49.3812, 55.9307]


async def main() -> None:
    scenes = await search_sentinel2_scenes(
        BBOX, "2026-10-05", "2027-01-03", max_cloud_cover=1, limit=3
    )
    for scene in scenes[:1]:
        print(scene.get("id"), flush=True)
        for band in ("swir16", "nir", "scl"):
            url = scene.get(f"{band}_url")
            with rasterio.open(url) as src:
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", src.crs, *BBOX, densify_pts=21
                )
                window = from_bounds(left, bottom, right, top, transform=src.transform)
                print(
                    band,
                    "crs",
                    src.crs,
                    "res",
                    src.res,
                    "bounds",
                    [round(v) for v in src.bounds],
                    "bbox_utm",
                    [round(v) for v in (left, bottom, right, top)],
                    "window",
                    [round(float(v), 1) for v in (window.col_off, window.row_off, window.width, window.height)],
                    flush=True,
                )
    scene = scenes[0]
    for band, default in (("swir16", 0.0001), ("nir", 0.0001), ("scl", 1.0)):
        scale, offset, source = _band_scaling(scene, band, default)
        data, _transform = _read_band_window(
            scene[f"{band}_url"], tuple(BBOX), scale, None, offset
        )
        if band == "scl":
            classes, counts = np.unique(np.rint(data).astype("int16"), return_counts=True)
            total = data.size
            print(
                "scl classes",
                json.dumps(
                    {int(c): round(float(n) / total * 100, 2) for c, n in zip(classes, counts, strict=False)}
                ),
                flush=True,
            )
        else:
            print(
                band,
                "scale",
                scale,
                "offset",
                offset,
                "source",
                source,
                "zero%",
                round(float((data == 0).mean()) * 100, 2),
                "neg%",
                round(float((data < 0).mean()) * 100, 2),
                "p50",
                round(float(np.percentile(data, 50)), 4),
                flush=True,
            )


asyncio.run(main())
