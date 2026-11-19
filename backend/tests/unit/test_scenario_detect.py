"""Определение сценария по свободному тексту запроса."""

from __future__ import annotations

import pytest

from glist.scenarios.detect import detect_analysis_type


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Покажи зоны затопления в Оренбурге в апреле 2024", "flood_extent"),
        ("Где разлилась река и подтоплены кварталы?", "flood_extent"),
        ("Как изменилась застройка Казани за 5 лет?", "built_up_change"),
        ("Сколько новых зданий появилось в промзоне?", "built_up_change"),
        ("Как изменилась зеленая зона Москвы за 5 лет?", "vegetation_change"),
        ("Покажи NDVI в парке", "vegetation_change"),
    ],
)
def test_detect_scenario_by_keywords(text: str, expected: str) -> None:
    result = detect_analysis_type(text)
    assert result["analysisType"] == expected
    assert 0.0 < float(result["confidence"]) <= 1.0
    assert result["matched"]


def test_unknown_text_falls_back_to_vegetation_without_confidence() -> None:
    """Неизвестный текст не выдаётся за уверенное определение."""
    result = detect_analysis_type("Проанализируй это место")
    assert result["analysisType"] == "vegetation_change"
    assert float(result["confidence"]) == 0.0
    assert result["candidates"] == []


def test_conflicting_keywords_are_reported_as_ambiguous() -> None:
    result = detect_analysis_type("Затопление и новая застройка в парке")
    assert result["ambiguous"] is True
    assert len(result["candidates"]) >= 2


def test_labels_are_human_readable() -> None:
    assert detect_analysis_type("паводок")["label"] == "Зоны затопления"
