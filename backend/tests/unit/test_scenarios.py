"""Явные сценарии анализа: типы, реестр, включение (без БД)."""

from __future__ import annotations

import pytest

from glist.scenarios.registry import (
    ScenarioUnavailable,
    capabilities,
    get_spec,
    is_enabled,
    run_scenario,
)
from glist.scenarios.types import (
    DEFAULT_ANALYSIS_TYPE,
    AnalysisType,
    analysis_type_label,
    class_labels,
    parse_analysis_type,
)


class _Settings:
    """Минимальный дубль настроек для проверки включения сценариев."""

    def __init__(self, built_up: bool = False, flood: bool = False) -> None:
        self.enable_built_up_change = built_up
        self.enable_flood_extent = flood


@pytest.mark.parametrize(
    "value",
    ["vegetation_change", "built_up_change", "flood_extent"],
)
def test_parse_known_types(value: str) -> None:
    assert parse_analysis_type(value) is not None


@pytest.mark.parametrize("value", [None, "", "ndvi", "flood", 42])
def test_parse_unknown_types(value: object) -> None:
    assert parse_analysis_type(value) is None


def test_default_type_is_vegetation() -> None:
    assert DEFAULT_ANALYSIS_TYPE is AnalysisType.VEGETATION_CHANGE


def test_class_labels_depend_on_scenario() -> None:
    vegetation = class_labels(AnalysisType.VEGETATION_CHANGE)
    built_up = class_labels(AnalysisType.BUILT_UP_CHANGE)
    flood = class_labels(AnalysisType.FLOOD_EXTENT)
    assert vegetation["retained"] != built_up["retained"]
    assert flood["new"] == "Временное затопление"
    # Неизвестный тип не должен ломать легенду: fallback — растительность.
    assert class_labels(None) == vegetation


def test_labels_are_human_readable() -> None:
    assert analysis_type_label("built_up_change") == "Анализ застройки"
    assert analysis_type_label("unknown") == "Изменение растительности"


def test_vegetation_is_always_enabled() -> None:
    assert is_enabled(AnalysisType.VEGETATION_CHANGE, _Settings()) is True


def test_new_scenarios_disabled_by_default() -> None:
    settings = _Settings()
    assert is_enabled(AnalysisType.BUILT_UP_CHANGE, settings) is False
    assert is_enabled(AnalysisType.FLOOD_EXTENT, settings) is False


def test_new_scenarios_enabled_by_setting() -> None:
    settings = _Settings(built_up=True, flood=True)
    assert is_enabled(AnalysisType.BUILT_UP_CHANGE, settings) is True
    assert is_enabled(AnalysisType.FLOOD_EXTENT, settings) is True


def test_capabilities_list_all_scenarios() -> None:
    items = capabilities(_Settings())
    assert {item["analysisType"] for item in items} == {
        "vegetation_change",
        "built_up_change",
        "flood_extent",
    }
    flags = {item["analysisType"]: item["enabled"] for item in items}
    assert flags["vegetation_change"] is True
    assert flags["built_up_change"] is False


def test_get_spec_has_own_module_for_new_scenarios() -> None:
    assert get_spec("built_up_change").module == "glist.scenarios.built_up"
    assert get_spec("flood_extent").entrypoint == "run_flood_extent"
    assert get_spec("vegetation_change").module is None


@pytest.mark.asyncio
async def test_disabled_scenario_is_not_silently_vegetation() -> None:
    with pytest.raises(ScenarioUnavailable):
        await run_scenario("built_up_change", {}, _Settings())
    with pytest.raises(ScenarioUnavailable):
        await run_scenario("flood_extent", {}, _Settings())
