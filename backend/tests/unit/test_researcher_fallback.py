"""Unit-тесты устойчивости Researcher к неполному JSON (без сети)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from glist.agents.researcher import _heuristic_params, researcher_node
from glist.tools.geocode import _cache_key, _pick_territory


def test_heuristic_extracts_kazan_and_last_year() -> None:
    params = _heuristic_params(
        "как изменилась зеленая зона в Казани за последний год?"
    )
    assert params["territories"][0]["name"] == "Казани"
    start = date.fromisoformat(params["period"]["start"])
    end = date.fromisoformat(params["period"]["end"])
    assert 360 <= (end - start).days <= 370


def test_geocode_cache_key_adds_country_filter_for_cyrillic() -> None:
    assert _cache_key("Казани") == "казани|ru"
    assert _cache_key("Kazan") == "kazan"


def test_pick_territory_prefers_admin_area_over_building() -> None:
    data = [
        {
            "display_name": "Казанский федеральный университет",
            "boundingbox": ["55.7888", "55.7931", "49.1172", "49.1230"],
            "addresstype": "building",
            "type": "yes",
        },
        {
            "display_name": "Казань",
            "boundingbox": ["55.60", "55.93", "48.82", "49.38"],
            "addresstype": "city",
            "type": "city",
        },
    ]
    picked = _pick_territory(data, "Казани")
    assert picked is not None
    assert picked["name"] == "Казань"
    assert picked["bbox"] == [48.82, 55.60, 49.38, 55.93]


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def generate(self, prompt: str, temperature: float = 0.0) -> str:
        return self.response


def _state() -> dict[str, Any]:
    return {"query_text": "Покажи изменение зелёных зон Казани"}


@pytest.mark.asyncio
async def test_researcher_prefers_relative_period_over_llm_dates() -> None:
    update = await researcher_node(
        {"query_text": "как изменилась зеленая зона в Казани за последний год?"},
        llm=_FakeLLM(
            '{"territories":[{"name":"Казань"}],'
            '"period":{"start":"2022-01-01","end":"2023-01-01"}}'
        ),
    )
    context = update["context"]
    assert context["territories"][0]["name"] == "Казань"
    start = date.fromisoformat(context["period"]["start"])
    end = date.fromisoformat(context["period"]["end"])
    assert 360 <= (end - start).days <= 370
    assert end.year >= 2025


@pytest.mark.asyncio
async def test_researcher_fills_missing_period_from_heuristic() -> None:
    update = await researcher_node(
        _state(),
        llm=_FakeLLM('{"territories":[{"name":"Казань"}]}'),
    )
    context = update["context"]
    assert context["territories"][0]["name"] == "Казань"
    start = date.fromisoformat(context["period"]["start"])
    end = date.fromisoformat(context["period"]["end"])
    assert start < end
    assert abs((end - datetime.now(UTC).date()).days) <= 1


@pytest.mark.asyncio
async def test_researcher_replaces_invalid_dates() -> None:
    update = await researcher_node(
        _state(),
        llm=_FakeLLM(
            '{"territories":[{"name":"Казань"}],'
            '"period":{"start":"вчера","end":"2023-01-01"}}'
        ),
    )
    context = update["context"]
    start = date.fromisoformat(context["period"]["start"])
    end = date.fromisoformat(context["period"]["end"])
    assert start < end
    assert end.year >= 2025


@pytest.mark.asyncio
async def test_researcher_rejects_empty_territory_names() -> None:
    update = await researcher_node(
        _state(),
        llm=_FakeLLM(
            '{"territories":[{"name":"   "}],'
            '"period":{"start":"2023-01-01","end":"2023-02-01"}}'
        ),
    )
    context = update["context"]
    assert context["territories"][0]["name"].strip()
    assert context["period"]["start"] == "2023-01-01"
    assert context["period"]["end"] == "2023-02-01"
