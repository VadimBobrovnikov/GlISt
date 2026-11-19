"""Реестр сценариев анализа: включение, доступность и вызов обработчика.

Новые сценарии независимы от расчёта NDVI: каждый имеет собственный модуль
и включается отдельной настройкой. Пока сценарий отключён или не реализован,
запрос не должен молча уходить в анализ растительности — обработчик
возвращает явную ошибку "сценарий недоступен".
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from glist.config import Settings, get_settings
from glist.scenarios.types import (
    DEFAULT_ANALYSIS_TYPE,
    AnalysisType,
    analysis_type_label,
    parse_analysis_type,
)

ScenarioHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ScenarioUnavailable(RuntimeError):
    """Сценарий отключён настройками или ещё не реализован."""


@dataclass(frozen=True)
class ScenarioSpec:
    """Описание сценария: настройка включения и точка входа обработчика."""

    analysis_type: AnalysisType
    description: str
    setting: str | None
    module: str | None
    entrypoint: str | None


_SPECS: dict[AnalysisType, ScenarioSpec] = {
    AnalysisType.VEGETATION_CHANGE: ScenarioSpec(
        analysis_type=AnalysisType.VEGETATION_CHANGE,
        description="NDVI-анализ зелёных зон (базовый сценарий, всегда включён)",
        setting=None,
        module=None,
        entrypoint=None,
    ),
    AnalysisType.BUILT_UP_CHANGE: ScenarioSpec(
        analysis_type=AnalysisType.BUILT_UP_CHANGE,
        description=(
            "Изменение застроенных/непроницаемых поверхностей по Sentinel-2 "
            "(спектральный прокси, не распознавание отдельных зданий)"
        ),
        setting="enable_built_up_change",
        module="glist.scenarios.built_up",
        entrypoint="run_built_up_change",
    ),
    AnalysisType.FLOOD_EXTENT: ScenarioSpec(
        analysis_type=AnalysisType.FLOOD_EXTENT,
        description=(
            "Временное затопление и постоянная вода по Sentinel-2 на дату "
            "события в сравнении с базовым периодом (без глубины и ущерба)"
        ),
        setting="enable_flood_extent",
        module="glist.scenarios.flood",
        entrypoint="run_flood_extent",
    ),
}


def get_spec(analysis_type: AnalysisType | str | None) -> ScenarioSpec:
    """Возвращает описание сценария (по умолчанию — анализ растительности)."""
    parsed = parse_analysis_type(analysis_type) or DEFAULT_ANALYSIS_TYPE
    return _SPECS[parsed]


def is_enabled(
    analysis_type: AnalysisType | str | None,
    settings: Settings | None = None,
) -> bool:
    """Включён ли сценарий в текущей конфигурации."""
    spec = get_spec(analysis_type)
    if spec.setting is None:
        return True
    if spec.module is None or spec.entrypoint is None:
        return False
    resolved = settings or get_settings()
    return bool(getattr(resolved, spec.setting, False))


def capabilities(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Список сценариев с признаком доступности (для интерфейса)."""
    resolved = settings or get_settings()
    result: list[dict[str, Any]] = []
    for analysis_type, spec in _SPECS.items():
        result.append(
            {
                "analysisType": analysis_type.value,
                "label": analysis_type_label(analysis_type),
                "description": spec.description,
                "enabled": is_enabled(analysis_type, resolved),
            }
        )
    return result


def handler_for(analysis_type: AnalysisType | str | None) -> ScenarioHandler:
    """Загружает обработчик сценария (не для растительности)."""
    spec = get_spec(analysis_type)
    if spec.module is None or spec.entrypoint is None:
        raise ScenarioUnavailable(
            "У сценария нет отдельного обработчика"
        )
    module = importlib.import_module(spec.module)
    handler = getattr(module, spec.entrypoint, None)
    if handler is None or not callable(handler):
        raise ScenarioUnavailable("Обработчик сценария не реализован")
    return handler  # type: ignore[no-any-return]


async def run_scenario(
    analysis_type: AnalysisType | str | None,
    state: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Выполняет сценарий и возвращает результат в форме состояния графа."""
    if not is_enabled(analysis_type, settings):
        raise ScenarioUnavailable(
            f"Сценарий «{analysis_type_label(analysis_type)}» пока недоступен"
        )
    return await handler_for(analysis_type)(state)
