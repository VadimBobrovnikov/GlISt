"""Unit-тесты search_sentinel2_scenes (httpx замокан, без сети)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from glist.tools.stac_search import (
    EARTH_SEARCH_STAC_URL,
    SENTINEL2_L2A_COLLECTION,
    search_sentinel2_scenes,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _feature(
    item_id: str,
    *,
    with_scl: bool = True,
    cloud: float = 7.5,
) -> dict[str, Any]:
    assets: dict[str, Any] = {
        "red": {"href": "https://example.com/B04.tif"},
        "green": {"href": "https://example.com/B03.tif"},
        "blue": {"href": "https://example.com/B02.tif"},
        "nir": {"href": "https://example.com/B08.tif"},
    }
    if with_scl:
        assets["scl"] = {"href": "https://example.com/SCL.tif"}
    return {
        "id": item_id,
        "properties": {
            "datetime": "2024-06-01T10:00:00Z",
            "eo:cloud_cover": cloud,
            "platform": "sentinel-2a",
            "proj:epsg": 32637,
        },
        "assets": assets,
    }


@pytest.mark.asyncio
async def test_search_returns_cog_urls_for_red_nir_scl() -> None:
    response = _Response({"features": [_feature("S2A_TILE")]})
    with patch(
        "glist.tools.stac_search.requests.get",
        return_value=response,
    ) as get_mock:
        scenes = await search_sentinel2_scenes(
            [37.5, 55.7, 37.7, 55.9],
            "2024-06-01",
            "2024-06-30",
            max_cloud_cover=15,
        )

    assert scenes == [
        {
            "id": "S2A_TILE",
            "datetime": "2024-06-01T10:00:00Z",
            "cloud_cover": 7.5,
            "platform": "sentinel-2a",
            "proj_epsg": 32637,
            "red_url": "https://example.com/B04.tif",
            "nir_url": "https://example.com/B08.tif",
            "scl_url": "https://example.com/SCL.tif",
            "green_url": "https://example.com/B03.tif",
            "blue_url": "https://example.com/B02.tif",
        }
    ]
    args, kwargs = get_mock.call_args
    assert args[0] == f"{EARTH_SEARCH_STAC_URL}/search"
    assert kwargs["params"]["collections"] == SENTINEL2_L2A_COLLECTION
    assert kwargs["params"]["bbox"] == "37.5,55.7,37.7,55.9"
    assert kwargs["params"]["datetime"] == (
        "2024-06-01T00:00:00Z/2024-06-30T23:59:59Z"
    )
    assert kwargs["params"]["limit"] == 10
    assert '"eo:cloud_cover"' in kwargs["params"]["query"]
    assert kwargs["headers"]["Accept"] == "application/geo+json"


@pytest.mark.asyncio
async def test_search_skips_scene_without_scl_asset() -> None:
    response = _Response(
        {"features": [_feature("ok"), _feature("no-scl", with_scl=False)]}
    )
    with patch(
        "glist.tools.stac_search.requests.get",
        return_value=response,
    ):
        scenes = await search_sentinel2_scenes(
            [37.5, 55.7, 37.7, 55.9],
            "2024-06-01",
            "2024-06-30",
        )

    assert [scene["id"] for scene in scenes] == ["ok"]


@pytest.mark.asyncio
async def test_search_returns_empty_list_on_stac_error() -> None:
    with (
        patch(
            "glist.tools.stac_search.requests.get",
            side_effect=RuntimeError("stac unavailable"),
        ),
        patch(
            "glist.tools.stac_search.asyncio.sleep",
            AsyncMock(),
        ),
    ):
        scenes = await search_sentinel2_scenes(
            [37.5, 55.7, 37.7, 55.9],
            "2024-06-01",
            "2024-06-30",
        )

    assert scenes == []
