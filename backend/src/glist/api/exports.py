"""Роутер экспорта результатов (задача T065).

Endpoints:
- ``POST /api/v1/runs/{runId}/exports`` — создание артефакта экспорта (PDF или GeoJSON).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.db.models import AnalysisRun

router = APIRouter(prefix="/api/v1")


class ExportCreate(BaseModel):
    """Входные данные для создания экспорта."""

    format: Literal["pdf", "geojson"]


# --- Endpoints ---


@router.post(
    "/runs/{runId}/exports",
    status_code=status.HTTP_201_CREATED,
    tags=["exports"],
)
async def create_export(  # noqa: B008
    runId: uuid.UUID,
    body: ExportCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Создаёт артефакт экспорта результата запуска."""
    result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.id == runId)  # type: ignore[arg-type]
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")

    if run.status not in ("завершён", "не прошёл проверку"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат не готов для экспорта",
        )

    # TODO: Тут должна быть реальная генерация файла экспорта
    # Сейчас возвращаем заглушку со ссылкой на скачивание
    export_id = uuid.uuid4()
    content_url = f"/api/v1/artifacts/{export_id}/content"

    return {
        "id": export_id,
        "runId": runId,
        "kind": body.format,
        "sizeBytes": 0,
        "isHeavy": False,
        "isDeleted": False,
        "contentUrl": content_url,
        "createdAt": run.created_at,
    }