"""Явные типы анализа GlISt (сценарии).

Тип анализа задаётся пользователем (карточка сценария) или, для свободного
текста, определяется отдельно и подтверждается до запуска. Значение хранится
в запросе и запуске и определяет обработчик, классы результата и легенду.
"""

from __future__ import annotations

from enum import StrEnum


class AnalysisType(StrEnum):
    """Поддерживаемые сценарии анализа."""

    VEGETATION_CHANGE = "vegetation_change"
    BUILT_UP_CHANGE = "built_up_change"
    FLOOD_EXTENT = "flood_extent"


DEFAULT_ANALYSIS_TYPE = AnalysisType.VEGETATION_CHANGE

_SCENARIO_LABELS: dict[AnalysisType, str] = {
    AnalysisType.VEGETATION_CHANGE: "Изменение растительности",
    AnalysisType.BUILT_UP_CHANGE: "Анализ застройки",
    AnalysisType.FLOOD_EXTENT: "Зоны затопления",
}

# Классы зон хранятся в общем виде retained/lost/new; смысл зависит от
# сценария и обязан отдаваться клиенту вместе с результатом.
_CLASS_LABELS: dict[AnalysisType, dict[str, str]] = {
    AnalysisType.VEGETATION_CHANGE: {
        "retained": "Растительность сохранилась",
        "lost": "Растительность исчезла",
        "new": "Появилась растительность",
    },
    AnalysisType.BUILT_UP_CHANGE: {
        "retained": "Застроено на обеих датах",
        "lost": "Застройка исчезла",
        "new": "Появилась застройка",
    },
    AnalysisType.FLOOD_EXTENT: {
        "retained": "Постоянная вода",
        "lost": "Вода была в базовый период, на дату события нет",
        "new": "Временное затопление",
    },
}

CLASS_CODES: tuple[str, ...] = ("retained", "lost", "new")


def parse_analysis_type(value: object) -> AnalysisType | None:
    """Разбирает тип анализа из запроса; неизвестное значение — ``None``."""
    if isinstance(value, AnalysisType):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    for analysis_type in AnalysisType:
        if analysis_type.value == normalized:
            return analysis_type
    return None


def analysis_type_label(analysis_type: AnalysisType | str | None) -> str:
    """Человекочитаемое название сценария."""
    parsed = parse_analysis_type(analysis_type) or DEFAULT_ANALYSIS_TYPE
    return _SCENARIO_LABELS[parsed]


def class_labels(analysis_type: AnalysisType | str | None) -> dict[str, str]:
    """Названия классов зон для сценария (легенда, экспорт)."""
    parsed = parse_analysis_type(analysis_type) or DEFAULT_ANALYSIS_TYPE
    return dict(_CLASS_LABELS[parsed])
