"""Роутер экспорта результатов (задача T065).

Endpoints:
- ``POST /api/v1/runs/{runId}/exports`` — создание артефакта экспорта (PDF или GeoJSON).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.config import get_settings
from glist.db.models import AnalysisRun, Artifact
from glist.services.artifact_store import save_artifact
from glist.services.export_service import (
    artifact_to_dict,
    load_export_data,
    write_export_file,
)

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
    run = await session.get(AnalysisRun, runId)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")

    if run.status not in ("завершён", "не прошёл проверку"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат не готов для экспорта",
        )

    data = await load_export_data(session, runId)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат ещё не готов",
        )

    settings = get_settings()
    path = write_export_file(data, body.format, settings.artifacts_dir)
    mime_type = (
        "application/pdf"
        if body.format == "pdf"
        else "application/geo+json"
    )
    artifact = await save_artifact(
        session,
        run_id=str(runId),
        filename=path.name,
        filepath=path,
        mime_type=mime_type,
    )
    await session.commit()
    return artifact_to_dict(artifact)


@router.get("/artifacts/{artifactId}/content")
async def download_artifact(  # noqa: B008
    artifactId: uuid.UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> FileResponse:
    """Отдаёт содержимое артефакта с Content-Disposition."""
    artifact = await session.get(Artifact, artifactId)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    if (
        artifact.retention_until is not None
        and artifact.retention_until < datetime.now(UTC)
    ):
        raise HTTPException(status_code=410, detail="Артефакт удалён по политике хранения")
    path = Path(artifact.uri)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Файл артефакта недоступен")
    media_type = {
        "pdf": "application/pdf",
        "geojson": "application/geo+json",
    }.get(artifact.kind, "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
    )