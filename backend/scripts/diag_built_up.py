"""Диагностика сценария застройки на реальных сценах (внутри контейнера)."""

from __future__ import annotations

import asyncio
import json

import numpy as np

from glist.agents.analyst import MAX_CLOUD_COVER, _window
from glist.scenarios import built_up
from glist.scenarios.built_up import _read_scene_index
from glist.tools.stac_search import search_sentinel2_scenes

BBOX = [48.8206, 55.6031, 49.3812, 55.9307]


async def main() -> None:
    for center in ("2019-11-19", "2026-11-19"):
        start, end = _window(__import__("datetime").date.fromisoformat(center), 45)
        scenes = await search_sentinel2_scenes(
            BBOX, start, end, max_cloud_cover=MAX_CLOUD_COVER, limit=6
        )
        print(center, "scenes:", len(scenes), flush=True)
        for scene in scenes[:2]:
            print(
                " ",
                scene.get("id"),
                "cloud", scene.get("cloud_cover"),
                "swir16_url", bool(scene.get("swir16_url")),
                flush=True,
            )
        semaphore = asyncio.Semaphore(2)
        for scene in scenes[:2]:
            entry = await _read_scene_index(scene, BBOX, semaphore)
            if entry is None:
                print("  read failed", scene.get("id"), flush=True)
                continue
            ndbi = entry["ndbi"]
            ndvi = entry["ndvi"]
            valid = entry["valid"]
            print(
                "  grid",
                json.dumps(entry["grid"]["shape"]),
                "res",
                entry["grid"]["resolution"],
                "valid%",
                round(float(valid.mean()) * 100, 2),
                flush=True,
            )
            finite = np.isfinite(ndbi)
            print(
                "  ndbi p5/p50/p95",
                [round(float(np.nanpercentile(ndbi, p)), 3) for p in (5, 50, 95)],
                "nan%",
                round(float((~finite).mean()) * 100, 2),
                "built%",
                round(
                    float(
                        (
                            (ndbi > built_up.BUILT_UP_THRESHOLD)
                            & (ndvi < built_up.VEGETATION_MAX_NDVI)
                        ).mean()
                    )
                    * 100,
                    2,
                ),
                flush=True,
            )


asyncio.run(main())
