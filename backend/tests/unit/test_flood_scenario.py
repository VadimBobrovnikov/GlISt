"""Сценарий затопления: дата события, объединение воды, отказы без данных."""

from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pytest
from affine import Affine

from glist.scenarios import flood
from glist.scenarios.flood import combine_water, parse_event_date


def _entry(water: list[list[bool]], valid: list[list[bool]]) -> dict[str, object]:
    return {
        "water": np.array(water, dtype=bool),
        "valid": np.array(valid, dtype=bool),
        "scene": {"id": "test"},
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("затопление 2026-04-05", date(2026, 4, 5)),
        ("паводок 05.04.2026", date(2026, 4, 5)),
        ("на 5 апреля 2026 года", date(2026, 4, 5)),
        ("затопление в апреле", None),
        ("31.02.2026", None),
    ],
)
def test_parse_event_date(text: str, expected: date | None) -> None:
    assert parse_event_date(text) == expected


def test_flood_needs_majority_of_valid_observations() -> None:
    """Вода считается только при подтверждении большинством валидных сцен."""
    entries = [
        _entry([[True]], [[True]]),
        _entry([[False]], [[True]]),
    ]
    combined = combine_water(entries)
    assert bool(combined["water"][0, 0]) is True

    noisy = [
        _entry([[True]], [[True]]),
        _entry([[False]], [[True]]),
        _entry([[False]], [[True]]),
    ]
    assert bool(combine_water(noisy)["water"][0, 0]) is False


def test_invalid_pixels_are_unknown_not_dry() -> None:
    entries = [_entry([[False]], [[False]])]
    combined = combine_water(entries)
    assert bool(combined["valid"][0, 0]) is False
    assert bool(combined["water"][0, 0]) is False


@pytest.mark.asyncio
async def test_missing_event_date_is_refused() -> None:
    """Без даты события анализ не запускается и не подменяется сезоном."""
    state = {
        "run_id": "00000000-0000-0000-0000-000000000000",
        "query_text": "Покажи зоны затопления в Казани за 2023 год",
        "context": {},
    }
    outcome = await flood.run_flood_extent(state)
    assert outcome["validation"]["outcome"] == "failed"
    assert outcome["context"]["change_zones"] == []
    assert outcome["validation"]["reasons"]
    assert "дата события" in outcome["validation"]["reasons"][0].lower()


@pytest.mark.asyncio
async def test_future_event_date_is_refused() -> None:
    state = {
        "run_id": "00000000-0000-0000-0000-000000000000",
        "query_text": "затопление 2099-04-05",
        "context": {},
    }
    outcome = await flood.run_flood_extent(state)
    assert outcome["validation"]["outcome"] == "failed"
    assert "не наступила" in outcome["validation"]["reasons"][0]


@pytest.mark.asyncio
async def test_baseline_selection_skips_snowy_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """База под снегом пропускается: берётся ближайшее бесснежное окно."""
    calls: list[tuple[str, str]] = []

    async def fake_search(
        bbox: list[float],
        start: str,
        end: str,
        **_: object,
    ) -> list[dict[str, object]]:
        calls.append((start, end))
        return [{"id": f"scene-{start}", "datetime": f"{start}T00:00:00Z"}]

    snow_by_start = {"2024-02-25": 0.93, "2024-01-26": 0.88, "2023-12-27": 0.02}

    async def fake_stats(
        scene: dict[str, object], bbox: list[float]
    ) -> dict[str, object]:
        scene_id = str(scene["id"])
        snow = snow_by_start.get(scene_id.replace("scene-", ""), 0.9)
        return {"snowShare": snow, "cloudShare": 0.02, "validShare": 0.6}

    monkeypatch.setattr(flood, "search_sentinel2_scenes", fake_search)
    monkeypatch.setattr(flood, "_scene_scl_stats", fake_stats)

    diagnostics: dict[str, object] = {}
    picked = await flood._select_baseline(date(2024, 4, 10), [1.0, 2.0, 3.0, 4.0], diagnostics)

    assert picked["accepted"] is True
    assert picked["window"] == {"from": "2023-12-27", "to": "2024-01-26"}
    statuses = [attempt["status"] for attempt in picked["attempts"]]
    assert statuses[0] == "snow"
    assert statuses[1] == "snow"
    assert statuses[-1] == "accepted"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_baseline_selection_refuses_when_all_windows_snowy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если бесснежного окна нет, база не выдаётся за пригодную."""

    async def fake_search(
        bbox: list[float],
        start: str,
        end: str,
        **_: object,
    ) -> list[dict[str, object]]:
        return [{"id": "scene", "datetime": f"{start}T00:00:00Z"}]

    async def fake_stats(
        scene: dict[str, object], bbox: list[float]
    ) -> dict[str, object]:
        return {"snowShare": 0.97, "cloudShare": 0.0, "validShare": 0.03}

    monkeypatch.setattr(flood, "search_sentinel2_scenes", fake_search)
    monkeypatch.setattr(flood, "_scene_scl_stats", fake_stats)

    diagnostics: dict[str, object] = {}
    picked = await flood._select_baseline(date(2024, 4, 10), [1.0, 2.0, 3.0, 4.0], diagnostics)

    assert picked["accepted"] is False
    assert picked["window"] is None
    assert all(attempt["status"] == "snow" for attempt in picked["attempts"])


@pytest.mark.asyncio
async def test_snow_is_unknown_not_dry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Снег/лёд (SCL 11) не считается наблюдением «воды нет»."""

    def fake_read(
        url: str,
        bbox: tuple[float, float, float, float],
        scale: float,
        out_shape: tuple[int, int] | None,
        offset: float,
    ) -> tuple[np.ndarray, Affine]:
        transform = Affine(0.0003, 0.0, bbox[0], 0.0, -0.0003, bbox[3])
        if url.endswith("SCL.tif"):
            return np.full((2, 2), 11.0), transform
        if url.endswith("B11.tif"):
            return np.full((2, 2), 0.2), transform
        if url.endswith("B03.tif"):
            return np.full((2, 2), 0.3), transform
        return np.full((2, 2), 0.05), transform

    monkeypatch.setattr(flood, "_read_band_window", fake_read)
    scene = {
        "id": "S2B_TEST",
        "green_url": "https://example/B03.tif",
        "swir16_url": "https://example/B11.tif",
        "nir_url": "https://example/B08.tif",
        "scl_url": "https://example/SCL.tif",
    }
    result = await flood._read_scene_water(
        scene, [1.0, 2.0, 3.0, 4.0], asyncio.Semaphore(1)
    )

    assert result is not None
    assert float(result["snowShare"]) == pytest.approx(1.0)
    assert bool(result["valid"].any()) is False
    assert bool(result["water"].any()) is False

