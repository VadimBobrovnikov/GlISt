"""Unit-тесты search_sentinel2_scenes (pystac-client замокан, без сети)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from glist.tools.stac_search import (
    EARTH_SEARCH_STAC_URL,
    SENTINEL2_L2A_COLLECTION,
    search_sentinel2_scenes,
)


class _Asset:
    def __init__(self, href: str) -> None:
        self.href = href


class _Item:
    def __init__(
        self,
        item_id: str,
        assets: dict[str, Any],
        properties: dict[str, Any],
    ) -> None:
        self.id = item_id
        self.assets = assets
        self.properties = properties


class _Search:
    def __init__(self, items: list[_Item]) -> None:
        self._items = items

    def items(self) -> list[_Item]:
        return self._items


def _item(item_id: str, *, with_scl: bool = True) -> _Item:
    assets: dict[str, Any] = {
        "red": _Asset("https://example.com/B04.tif"),
        "nir": _Asset("https://example.com/B08.tif"),
    }
    if with_scl:
        assets["scl"] = _Asset("https://example.com/SCL.tif")
    return _Item(
        item_id,
        assets,
        {
            "datetime": "2024-06-01T10:00:00Z",
            "eo:cloud_cover": 7.5,
            "platform": "sentinel-2a",
        },
    )


@pytest.mark.asyncio
async def test_search_returns_cog_urls_for_red_nir_scl() -> None:
    client = MagicMock()
    client.search.return_value = _Search([_item("S2A_TILE")])
    with patch(
        "glist.tools.stac_search.pystac_client.Client.open",
        return_value=client,
    ) as open_mock:
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
            "red_url": "https://example.com/B04.tif",
            "nir_url": "https://example.com/B08.tif",
            "scl_url": "https://example.com/SCL.tif",
        }
    ]
    open_mock.assert_called_once_with(EARTH_SEARCH_STAC_URL)
    client.search.assert_called_once_with(
        collections=[SENTINEL2_L2A_COLLECTION],
        bbox=[37.5, 55.7, 37.7, 55.9],
        datetime="2024-06-01/2024-06-30",
        query={"eo:cloud_cover": {"lt": 15}},
        limit=10,
    )


@pytest.mark.asyncio
async def test_search_skips_scene_without_scl_asset() -> None:
    client = MagicMock()
    client.search.return_value = _Search(
        [_item("ok"), _item("no-scl", with_scl=False)]
    )
    with patch(
        "glist.tools.stac_search.pystac_client.Client.open",
        return_value=client,
    ):
        scenes = await search_sentinel2_scenes(
            [37.5, 55.7, 37.7, 55.9],
            "2024-06-01",
            "2024-06-30",
        )

    assert [scene["id"] for scene in scenes] == ["ok"]


@pytest.mark.asyncio
async def test_search_returns_empty_list_on_stac_error() -> None:
    with patch(
        "glist.tools.stac_search.pystac_client.Client.open",
        side_effect=RuntimeError("stac unavailable"),
    ):
        scenes = await search_sentinel2_scenes(
            [37.5, 55.7, 37.7, 55.9],
            "2024-06-01",
            "2024-06-30",
        )

    assert scenes == []
