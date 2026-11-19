"""Определение сценария по тексту запроса (без выбранной карточки)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from glist.api.deps import get_current_user
from glist.scenarios.detect import detect_analysis_type

router = APIRouter(prefix="/api/v1", tags=["scenarios"])


class DetectRequest(BaseModel):
    """Текст запроса для определения сценария."""

    text: str = Field(min_length=1, max_length=2000)


@router.post("/analysis-types/detect")
async def detect_scenario(
    body: DetectRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Предлагает сценарий по тексту; окончательный выбор — за пользователем."""
    return detect_analysis_type(body.text)
