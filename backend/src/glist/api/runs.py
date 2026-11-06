"""Роутер запусков анализа (задача T064).

Endpoints:
- ``GET /api/v1/runs/{runId}`` — статус запуска.
- ``GET /api/v1/runs/{runId}/result`` — результат или 409, если ещё не готов.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.db.models import AnalysisResult, AnalysisRun

router = APIRouter(prefix="/api/v1")


# --- Pydantic schemas ---


class Run(BaseModel):
    """Схема Run из контракта (упрощённая)."""

    id: uuid.UUID
    queryId: uuid.UUID
    parentRunId: uuid.UUID | None = None
    reusedResultId: uuid.UUID | None = None
    status: str
    statusReason: str | None = None
    queuePosition: int | None = None
    queuedAt: Any | None = None
    startedAt: Any | None = None
    finishedAt: Any | None = None
    attemptCount: int = 0
    rejectionCategory: str | None = None
    traceId: str
    settingsHash: str
    paramsHash: str
    seed: int
    ragIndexVersion: str
    modelVersions: dict[str, Any]
    isReference: bool = False
    isPinned: bool = False
    retentionUntil: Any | None = None
    purgeNoticeAt: Any | None = None
    createdAt: Any
    stages: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []


class AnalysisResultSchema(BaseModel):
    """Схема AnalysisResult (упрощённая)."""

    id: uuid.UUID
    runId: uuid.UUID
    territories: list[dict[str, Any]]
    aggregate: dict[str, Any]
    createdAt: Any


# --- Endpoints ---


@router.get(
    "/runs/{runId}",
    response_model=Run,
    tags=["runs"],
)
async def get_run(  # noqa: B008
    runId: uuid.UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> Run:
    """Возвращает статус запуска."""
    result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.id == runId)  # type: ignore[arg-type]
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return Run(
        id=run.id,
        queryId=run.query_id,
        parentRunId=run.parent_run_id,
        reusedResultId=run.reused_result_id,
        status=run.status,
        statusReason=run.status_reason,
        queuePosition=run.queue_position,
        queuedAt=run.queued_at,
        startedAt=run.started_at,
        finishedAt=run.finished_at,
        attemptCount=run.attempt_count,
        rejectionCategory=run.rejection_category,
        traceId=run.trace_id,
        settingsHash=run.settings_hash,
        paramsHash=run.params_hash,
        seed=run.seed,
        ragIndexVersion=run.rag_index_version,
        modelVersions=run.model_versions,
        isReference=run.is_reference,
        isPinned=run.is_pinned,
        retentionUntil=run.retention_until,
        purgeNoticeAt=run.purge_notice_at,
        createdAt=run.created_at,
        stages=[],
        rejections=[],
    )


@router.get(
    "/runs/{runId}/result",
    response_model=AnalysisResultSchema,
    tags=["runs"],
)
async def get_run_result(  # noqa: B008
    runId: uuid.UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> AnalysisResultSchema:
    """Возвращает результат анализа или 409, если ещё не готов."""
    result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.id == runId)  # type: ignore[arg-type]
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")

    if run.status not in ("завершён", "не прошёл проверку"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат ещё не готов",
        )

    result_row = await session.execute(
        select(AnalysisResult).where(AnalysisResult.run_id == runId)  # type: ignore[arg-type]
    )
    analysis_result = result_row.scalar_one_or_none()
    if analysis_result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат ещё не готов",
        )

    return AnalysisResultSchema(
        id=analysis_result.id,
        runId=analysis_result.run_id,
        territories=[],
        aggregate={},
        createdAt=analysis_result.created_at,
    )