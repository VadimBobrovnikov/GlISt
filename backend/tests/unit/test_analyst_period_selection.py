"""Unit-тесты подбора сопоставимых сезонных окон (без сети)."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from glist.agents import analyst as analyst_module

_WINTER = {"id": "W", "datetime": "2019-01-22T00:00:00Z"}
_SPRING_2023 = {"id": "E", "datetime": "2023-04-21T00:00:00Z"}
_SPRING_2019 = {"id": "S", "datetime": "2019-04-20T00:00:00Z"}


def _candidate_centers() -> list[tuple[date, date]]:
    return [
        (start, end)
        for start, end, _month, _day in analyst_module._season_candidates(
            date(2019, 1, 1), date(2023, 4, 14)
        )
    ]


def test_season_candidates_prefer_requested_end_anchor() -> None:
    candidates = _candidate_centers()
    assert candidates[0] == (date(2019, 4, 14), date(2023, 4, 14))
    assert len(candidates) == len(set(candidates))


def test_season_candidates_stay_inside_requested_period() -> None:
    start = date(2019, 1, 1)
    end = date(2023, 4, 14)
    for candidate_start, candidate_end in _candidate_centers():
        assert start <= candidate_start < candidate_end <= end


@pytest.mark.asyncio
async def test_search_uses_requested_dates_when_seasons_match() -> None:
    search = AsyncMock(side_effect=[[_SPRING_2019], [_SPRING_2023]])
    with patch.object(analyst_module, "_search_center", search):
        start_scenes, end_scenes, info = (
            await analyst_module._search_comparable_periods(
                [37.5, 55.7, 37.6, 55.8],
                date(2019, 4, 14),
                date(2023, 4, 14),
            )
        )
    assert start_scenes == [_SPRING_2019]
    assert end_scenes == [_SPRING_2023]
    assert info["method"] == "requested"
    assert info["seasonAdjusted"] is False
    assert search.call_count == 2


@pytest.mark.asyncio
async def test_search_switches_to_same_season_and_reports_it() -> None:
    search = AsyncMock(side_effect=[[_WINTER], [_SPRING_2023], [_SPRING_2019]])
    with patch.object(analyst_module, "_search_center", search):
        start_scenes, end_scenes, info = (
            await analyst_module._search_comparable_periods(
                [37.5, 55.7, 37.6, 55.8],
                date(2019, 1, 1),
                date(2023, 4, 14),
            )
        )
    assert start_scenes == [_SPRING_2019]
    assert end_scenes == [_SPRING_2023]
    assert info["method"] == "same_season"
    assert info["seasonAdjusted"] is True
    assert info["usedCenters"] == {
        "start": "2019-04-14",
        "end": "2023-04-14",
    }
    assert info["requestedCenters"]["start"] == "2019-01-01"
    assert info["usedSceneDates"] == {
        "start": ["2019-04-20"],
        "end": ["2023-04-21"],
    }
    # Третий вызов — окно подбора с ограничением пределами запроса.
    third_call = search.call_args_list[2]
    assert third_call.args[3] == (date(2019, 1, 1), date(2023, 4, 14))


@pytest.mark.asyncio
async def test_search_falls_back_to_requested_with_mismatch_flag() -> None:
    search = AsyncMock(
        side_effect=[[_WINTER], [_SPRING_2023], [], [], [], []],
    )
    with patch.object(analyst_module, "_search_center", search):
        start_scenes, end_scenes, info = (
            await analyst_module._search_comparable_periods(
                [37.5, 55.7, 37.6, 55.8],
                date(2019, 1, 1),
                date(2023, 4, 14),
            )
        )
    assert start_scenes == [_WINTER]
    assert end_scenes == [_SPRING_2023]
    assert info["method"] == "requested_season_mismatch"
    assert info["seasonAdjusted"] is False
    assert info["attempted"]


@pytest.mark.asyncio
async def test_search_does_not_replace_missing_requested_data() -> None:
    search = AsyncMock(side_effect=[[], [_SPRING_2023]])
    with patch.object(analyst_module, "_search_center", search):
        start_scenes, _end_scenes, info = (
            await analyst_module._search_comparable_periods(
                [37.5, 55.7, 37.6, 55.8],
                date(2019, 1, 1),
                date(2023, 4, 14),
            )
        )
    assert start_scenes == []
    assert info["method"] == "requested_missing"
    assert search.call_count == 2


def _period(values: list[dict[str, Any]]) -> dict[str, Any]:
    return analyst_module._period_season(values)


def test_period_season_rejects_mixed_seasons() -> None:
    mixed = _period(
        [
            {"datetime": "2023-02-20T00:00:00Z"},
            {"datetime": "2023-04-01T00:00:00Z"},
        ]
    )
    assert mixed["season"] is None
    assert mixed["consistent"] is False
    assert mixed["seasons"] == ["зима", "весна"]
