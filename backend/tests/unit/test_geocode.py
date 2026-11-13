"""Unit-тесты Nominatim-геокодирования (T050), без реальной сети."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from glist.config import get_settings
from glist.tools.geocode import _bbox_from_boundingbox, geocode_territory


class _Response:
    status_code = 200

    def json(self) -> list[dict[str, Any]]:
        return [
            {
                "display_name": "Москва, Россия",
                "boundingbox": ["55.55", "55.95", "37.35", "37.95"],
                "lat": "55.75",
                "lon": "37.62",
            }
        ]


@pytest.mark.asyncio
async def test_geocode_territory_returns_bbox_and_user_agent(
    required_env: dict[str, str],
) -> None:
    get_settings.cache_clear()
    client = MagicMock()
    client.get = AsyncMock(return_value=_Response())
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=client)
    context_manager.__aexit__ = AsyncMock(return_value=False)

    try:
        with patch(
            "glist.tools.geocode.httpx.AsyncClient",
            return_value=context_manager,
        ):
            payload = await geocode_territory("Москва", use_cache=False)
    finally:
        get_settings.cache_clear()

    assert payload is not None
    assert payload["bbox"] == [37.35, 55.55, 37.95, 55.95]
    assert payload["provider"] == "nominatim"
    client.get.assert_awaited_once()
    _args, kwargs = client.get.call_args
    assert kwargs["headers"]["User-Agent"] == required_env["OSM_USER_AGENT"]
    assert kwargs["params"]["q"] == "Москва"


def test_bbox_from_boundingbox_rejects_invalid() -> None:
    assert _bbox_from_boundingbox(["55", "55", "37", "38"]) is None
    assert _bbox_from_boundingbox("bad") is None
