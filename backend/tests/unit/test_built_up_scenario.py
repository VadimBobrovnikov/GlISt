"""Сценарий застройки: классификация на фиксированных массивах и отказы."""

from __future__ import annotations

import numpy as np
import pytest

from glist.scenarios import built_up
from glist.scenarios.built_up import (
    BUILT_UP_THRESHOLD,
    VEGETATION_MAX_NDVI,
    classify_change,
)


def _classify(
    start_ndbi: list[list[float]],
    start_ndvi: list[list[float]],
    end_ndbi: list[list[float]],
    end_ndvi: list[list[float]],
    valid: list[list[bool]] | None = None,
    aoi: list[list[bool]] | None = None,
) -> dict[str, np.ndarray]:
    start_n = np.array(start_ndbi, dtype="float32")
    end_n = np.array(end_ndbi, dtype="float32")
    start_v = np.array(start_ndvi, dtype="float32")
    end_v = np.array(end_ndvi, dtype="float32")
    shape = start_n.shape
    valid_arr = np.ones(shape, dtype=bool) if valid is None else np.array(valid)
    aoi_arr = np.ones(shape, dtype=bool) if aoi is None else np.array(aoi)
    return classify_change(start_n, start_v, end_n, end_v, valid_arr, aoi_arr)


def test_new_construction_is_detected() -> None:
    """Пустой участок в начале и застройка в конце — класс «появилась»."""
    result = _classify(
        [[0.02]], [[0.55]], [[0.25]], [[0.10]]
    )
    assert bool(result["new"][0, 0]) is True
    assert bool(result["lost"][0, 0]) is False
    assert bool(result["retained"][0, 0]) is False


def test_retained_construction() -> None:
    result = _classify([[0.25]], [[0.10]], [[0.22]], [[0.12]])
    assert bool(result["retained"][0, 0]) is True


def test_demolition_is_lost_construction() -> None:
    result = _classify([[0.25]], [[0.10]], [[0.01]], [[0.60]])
    assert bool(result["lost"][0, 0]) is True


def test_vegetation_is_not_construction_even_with_high_ndbi() -> None:
    """NDVI выше порога исключает пиксель: снижение NDVI само по себе — не застройка."""
    ndvi_high = VEGETATION_MAX_NDVI + 0.2
    result = _classify([[0.3]], [[ndvi_high]], [[0.3]], [[ndvi_high]])
    assert not result["retained"].any()
    assert not result["new"].any()
    assert not result["lost"].any()


def test_ndbi_below_threshold_is_not_construction() -> None:
    ndbi_low = BUILT_UP_THRESHOLD - 0.02
    result = _classify([[ndbi_low]], [[0.1]], [[ndbi_low]], [[0.1]])
    assert not result["retained"].any()


def test_invalid_pixels_become_unknown_not_absent() -> None:
    """Облачный пиксель не попадает ни в класс изменений, ни в «нет застройки»."""
    result = _classify(
        [[0.25, 0.25]],
        [[0.10, 0.10]],
        [[0.25, 0.25]],
        [[0.10, 0.10]],
        valid=[[True, False]],
    )
    assert bool(result["unknown"][0, 1]) is True
    assert bool(result["retained"][0, 1]) is False
    assert bool(result["retained"][0, 0]) is True


def test_area_outside_aoi_is_unknown() -> None:
    result = _classify(
        [[0.3, 0.3]],
        [[0.1, 0.1]],
        [[0.3, 0.3]],
        [[0.1, 0.1]],
        aoi=[[True, False]],
    )
    assert bool(result["unknown"][0, 1]) is True
    assert bool(result["retained"][0, 1]) is False


def test_comparable_centers_keep_the_same_season() -> None:
    """«До» переносится на месяц/день «после» внутри запрошенного периода."""
    from datetime import date

    before, after, info = built_up._comparable_centers(
        date(2019, 1, 1), date(2026, 11, 19)
    )
    assert after == date(2026, 11, 19)
    assert before == date(2019, 11, 19)
    assert info["seasonAdjusted"] is True
    assert info["usedCenters"]["start"] == "2019-11-19"


def test_comparable_centers_respect_period_bounds() -> None:
    """Сдвиг не выходит за пределы запроса: иначе берётся исходная дата."""
    from datetime import date

    before, _after, info = built_up._comparable_centers(
        date(2026, 11, 1), date(2026, 11, 19)
    )
    assert before == date(2026, 11, 1)
    assert info["seasonAdjusted"] is False


@pytest.mark.asyncio
async def test_no_scenes_is_data_quality_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отсутствие сцен — это отсутствие данных, а не «изменений нет»."""

    async def _no_scenes(
        bbox: list[float], start: str, end: str
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(built_up, "_search_period_scenes", _no_scenes)

    state = {
        "run_id": "00000000-0000-0000-0000-000000000000",
        "context": {
            # Подтверждённая пользователем AOI: повторный геокодинг не нужен.
            "resolved_territory": {
                "name": "Тестовая территория",
                "bbox": [37.60, 55.70, 37.70, 55.80],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [37.60, 55.70],
                            [37.70, 55.70],
                            [37.70, 55.80],
                            [37.60, 55.80],
                            [37.60, 55.70],
                        ]
                    ],
                },
            },
            "period": {"start": "2024-01-01", "end": "2026-01-01"},
        },
    }
    outcome = await built_up.run_built_up_change(state)
    validation = outcome["validation"]
    assert validation["outcome"] == "failed"
    assert validation["category"] == "data_quality"
    assert outcome["context"]["change_zones"] == []
    diagnostics = outcome["context"]["analysis_diagnostics"]
    assert diagnostics["technicalSuccess"] is True
    assert diagnostics["dataQualityAssessed"] is True
