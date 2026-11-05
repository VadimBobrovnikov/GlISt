"""Contract-тесты API (задача T039).

Проверяют соответствие эндпоинтов контракту:
- GET /api/v1/health — код 200 и структура JSON.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from glist.main import app


@pytest.fixture
async def client() -> Any:
    """HTTP-клиент поверх ASGI-приложения FastAPI."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_returns_ok(client: Any, required_env: dict[str, str]) -> None:
    """GET /api/v1/health возвращает 200 и поле status."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_health_response_structure(
    client: Any, required_env: dict[str, str]
) -> None:
    """Тело ответа health содержит только ожидаемое поле status."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status"}