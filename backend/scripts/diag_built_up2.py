"""Диагностика покрытия сценария застройки на общей сетке (в контейнере)."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import numpy as np

from glist.agents.analyst import _aoi_mask, _window
from glist.scenarios import built_up
from glist.scenarios.grid import affine_of, target_grid, warp_mask_to_grid
from glist.tools.stac_search import search_sentinel2_scenes

BBOX = [48.8206, 55.6031, 49.3812, 55.9307]


async def main() -> None:
    target = target_grid(BBOX, 30.0)
    transform = affine_of(target)
    aoi = _aoi_mask(None, transform, tuple(target["shape"]), target["crs"])
    print("target", target["shape"], "aoi px", int(aoi.sum()), flush=True)
    for center in ("2019-11-19", "2026-11-19"):
        start, end = _window(date.fromisoformat(center), 45)
        scenes = await search_sentinel2_scenes(
            BBOX, start, end, max_cloud_cover=30, limit=8
        )
        print(center, "scenes", len(scenes), flush=True)
        semaphore = asyncio.Semaphore(3)
        union = np.zeros(tuple(target["shape"]), dtype=bool)
        for scene in scenes:
            entry = await built_up._read_scene_index(scene, BBOX, semaphore)
            if entry is None:
                print("  ", scene.get("id"), "read failed", flush=True)
                continue
            warped = warp_mask_to_grid(entry["valid"], entry["grid"], target)
            inside = int((warped & aoi).sum())
            union = union | warped
            print(
                "  ",
                scene.get("id"),
                scene.get("proj_epsg"),
                "cloud",
                round(float(scene.get("cloud_cover") or 0), 2),
                "raw valid%",
                round(float(entry["valid"].mean()) * 100, 2),
                "aoi valid%",
                round(inside / max(1, int(aoi.sum())) * 100, 2),
                flush=True,
            )
        print(
            "  union aoi%",
            round(float((union & aoi).sum()) / max(1, int(aoi.sum())) * 100, 2),
            flush=True,
        )


asyncio.run(main())
