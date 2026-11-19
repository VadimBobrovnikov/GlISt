"""Определение сценария по свободному тексту запроса.

Нужно, чтобы запрос без выбранной карточки не уходил молча в анализ зелени:
сценарий определяется по ключевым словам, показывается пользователю и
подтверждается. Это эвристика, а не понимание текста: при неуверенности
возвращается ``ambiguous``/``confidence=0``, и решение остаётся за человеком.
"""

from __future__ import annotations

from glist.scenarios.types import (
    DEFAULT_ANALYSIS_TYPE,
    AnalysisType,
    analysis_type_label,
)

# Ключевые слова сценариев (в нижнем регистре, без «ё»).
_KEYWORDS: dict[AnalysisType, tuple[str, ...]] = {
    AnalysisType.FLOOD_EXTENT: (
        "затоплен",
        "затопит",
        "паводок",
        "наводнен",
        "половод",
        "подтоплен",
        "разлив",
        "вышел из берегов",
        "уровень воды",
    ),
    AnalysisType.BUILT_UP_CHANGE: (
        "застройк",
        "застроен",
        "здани",
        "строительств",
        "строительн",
        "новостройк",
        "промзон",
        "непроницаем",
        "асфальт",
    ),
    AnalysisType.VEGETATION_CHANGE: (
        "зелен",
        "растительн",
        "ndvi",
        "лес",
        "парк",
        "газон",
        "дерев",
        "озелен",
        "сквер",
    ),
}


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def detect_analysis_type(text: str) -> dict[str, object]:
    """Возвращает предлагаемый сценарий, уверенность и совпавшие слова."""
    normalized = _normalize(text)
    scores: dict[AnalysisType, int] = {}
    matched: dict[AnalysisType, list[str]] = {}
    for analysis_type, words in _KEYWORDS.items():
        hits = [word for word in words if word in normalized]
        if hits:
            scores[analysis_type] = len(hits)
            matched[analysis_type] = hits

    if not scores:
        return {
            "analysisType": DEFAULT_ANALYSIS_TYPE.value,
            "label": analysis_type_label(DEFAULT_ANALYSIS_TYPE),
            "confidence": 0.0,
            "matched": [],
            "candidates": [],
            "ambiguous": False,
            "reason": (
                "Ключевые слова сценария не найдены: по умолчанию предлагается "
                "анализ растительности. Уточните сценарий, если это не так."
            ),
        }

    ordered = sorted(scores, key=lambda item: (-scores[item], item.value))
    best = ordered[0]
    total = sum(scores.values())
    confidence = round(scores[best] / total, 4)
    ambiguous = len(ordered) > 1 and scores[ordered[1]] == scores[best]
    candidates = [
        {
            "analysisType": item.value,
            "label": analysis_type_label(item),
            "score": scores[item],
            "matched": matched[item],
        }
        for item in ordered
    ]
    return {
        "analysisType": best.value,
        "label": analysis_type_label(best),
        "confidence": confidence,
        "matched": matched[best],
        "candidates": candidates,
        "ambiguous": ambiguous,
        "reason": (
            "Сценарий определён по ключевым словам запроса; проверьте его перед "
            "запуском."
        ),
    }
