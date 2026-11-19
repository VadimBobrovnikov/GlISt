"""Подбор события для проверки затопления: сцены события и базового периода."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from glist.tools.stac_search import search_sentinel2_scenes

CANDIDATES = [
    ("Уссурийск", [131.87, 43.72, 132.09, 43.85], date(2023, 8, 12)),
    ("Оренбург", [55.02, 51.72, 55.22, 51.83], date(2024, 4, 10)),
    ("Якутск", [129.60, 62.00, 129.85, 62.10], date(2024, 5, 20)),
    ("Благовещенск", [127.45, 50.22, 127.62, 50.32], date(2024, 8, 20)),
    ("Новосибирск", [82.80, 54.95, 83.05, 55.08], date(2024, 4, 15)),
]


async def main() -> None:
    for name, bbox, event in CANDIDATES:
        event_scenes = await search_sentinel2_scenes(
            bbox,
            (event - timedelta(days=5)).isoformat(),
            (event + timedelta(days=5)).isoformat(),
            max_cloud_cover=60,
            limit=6,
        )
        base_scenes = await search_sentinel2_scenes(
            bbox,
            (event - timedelta(days=45)).isoformat(),
            (event - timedelta(days=15)).isoformat(),
            max_cloud_cover=60,
            limit=6,
        )
        print(
            name,
            event.isoformat(),
            "event scenes",
            len(event_scenes),
            [str(s.get("datetime"))[:10] for s in event_scenes],
            "baseline",
            len(base_scenes),
            [str(s.get("datetime"))[:10] for s in base_scenes],
            flush=True,
        )


asyncio.run(main())
