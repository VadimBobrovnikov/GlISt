"""Unit-тесты общего выбора территории (без сети)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from glist.agents.researcher import extract_territory_params
from glist.config import get_settings
from glist.tools.geocode import GeocodeError, _fetch_nominatim, select_territory


def _item(
    display: str,
    bbox: list[str],
    kind: str,
    *,
    lat: str = "55.0",
    lon: str = "49.0",
    address: dict[str, str] | None = None,
    geometry: bool = True,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "display_name": display,
        "name": display.split(",")[0],
        "boundingbox": bbox,
        "addresstype": kind,
        "type": kind,
        "lat": lat,
        "lon": lon,
        "address": address or {},
    }
    if geometry:
        south, north, west, east = (float(value) for value in bbox)
        item["geojson"] = {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        }
    return item


def test_island_is_not_replaced_by_same_named_district() -> None:
    data = [
        _item(
            "Русский, Первомайский район, Владивосток, Россия",
            ["43.0", "43.1", "131.8", "131.9"],
            "island",
            lat="43.05",
            lon="131.85",
            address={"city": "Владивосток"},
        ),
        _item(
            "Русский, Первомайский район, Владивосток, Россия",
            ["43.0", "43.2", "131.8", "132.0"],
            "suburb",
            lat="43.1",
            lon="131.9",
            address={"city": "Владивосток"},
        ),
    ]
    picked = select_territory(
        data,
        "Русский",
        "island",
        "Владивосток",
        [131.5, 42.9, 132.2, 43.3],
    )
    assert picked is not None
    assert picked["kind"] == "island"


def test_city_in_genitive_and_typo_is_ranked_by_name() -> None:
    data = [
        _item(
            "Казань, городской округ Казань, Татарстан, Россия",
            ["55.60", "55.93", "48.82", "49.38"],
            "city",
            address={"city": "Казань", "state": "Татарстан"},
        )
    ]
    picked = select_territory(data, "Козани", "city", None)
    assert picked is not None
    assert picked["name"] == "Казань"
    assert picked["score"] > 0.7


def test_parent_mismatch_is_rejected_even_with_same_name() -> None:
    data = [
        _item(
            "Восточный, Омский район, Омская область, Россия",
            ["55.0", "55.1", "73.0", "73.2"],
            "suburb",
            address={"state": "Омская область"},
        )
    ]
    picked = select_territory(
        data,
        "Восточный",
        "district",
        "Улан-Удэ",
        [107.4, 51.7, 108.0, 52.1],
    )
    assert picked is None


def test_ambiguous_candidates_are_returned_with_geometry() -> None:
    data = [
        _item(
            "Парк Горького, Москва, Россия",
            ["55.72", "55.74", "37.59", "37.61"],
            "park",
            address={"city": "Москва"},
        ),
        _item(
            "Парк Горького, Казань, Россия",
            ["55.75", "55.77", "49.10", "49.12"],
            "park",
            address={"city": "Казань"},
        ),
    ]
    picked = select_territory(data, "Парк Горького", "park", None)
    assert picked is not None
    assert picked["ambiguous"] is True
    assert len(picked["candidates"]) == 2
    assert all(
        isinstance(candidate.get("geometry"), dict)
        for candidate in picked["candidates"]
    )


def test_point_only_is_marked_separately() -> None:
    data = [
        _item(
            "Остров Малый, Владивосток, Россия",
            ["43.0", "43.01", "131.8", "131.81"],
            "island",
            geometry=False,
            address={"city": "Владивосток"},
        )
    ]
    picked = select_territory(data, "Малый", "island", "Владивосток")
    assert picked is not None
    assert picked["pointOnly"] is True
    assert picked["geometry"] is None


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def generate(self, prompt: str, temperature: float = 0.0) -> str:
        return self.response


@pytest.mark.asyncio
async def test_extract_territory_accepts_free_text_type_and_parent() -> None:
    extracted = await extract_territory_params(
        "зелень на острове Русский во Владивастоке",
        llm=_FakeLLM(
            '{"name":"Русский","normalizedName":"русский",'
            '"type":"island","parent":"Владивосток"}'
        ),
    )
    assert extracted is not None
    assert extracted["name"] == "Русский"
    assert extracted["type"] == "island"
    assert extracted["parent"] == "Владивосток"


@pytest.mark.asyncio
async def test_extract_territory_does_not_invent_missing_name() -> None:
    extracted = await extract_territory_params(
        "покажи что-нибудь",
        llm=_FakeLLM('{"type":"park"}'),
    )
    assert extracted is None


@pytest.mark.asyncio
async def test_geocoder_http_429_is_not_missing_object(
    required_env: dict[str, str],
) -> None:
    get_settings.cache_clear()
    response = MagicMock()
    response.status_code = 429
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=client)
    context_manager.__aexit__ = AsyncMock(return_value=False)
    try:
        with (
            patch(
                "glist.tools.geocode.httpx.AsyncClient",
                return_value=context_manager,
            ),
            pytest.raises(GeocodeError, match="HTTP 429"),
        ):
            await _fetch_nominatim("Парк Горького")
    finally:
        get_settings.cache_clear()
