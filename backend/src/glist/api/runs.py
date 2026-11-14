"""Роутер запусков анализа (задача T064).

Endpoints:
- ``GET /api/v1/runs/{runId}`` — статус запуска.
- ``GET /api/v1/runs/{runId}/result`` — результат или 409, если ещё не готов.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.db.models import AnalysisResult, AnalysisRun, ChangeZone, ResultTerritory

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
    """Схема AnalysisResult с территориями и GeoJSON-геометрией."""

    id: uuid.UUID
    runId: uuid.UUID
    mode: str = "single"
    territoriesCount: int = 0
    hasOverlap: bool = False
    crs: str = "EPSG:4326"
    summaryText: str = ""
    explanationText: str = ""
    sources: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    validationOutcome: str = "passed"
    territories: list[dict[str, Any]] = []
    aggregate: dict[str, Any] = {}
    bbox: list[float] | None = None
    coverage: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    qualityLimitations: list[str] = []
    createdAt: Any = None


def _source_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Приводит источник из sources_summary к виду контракта API."""
    return {
        "source": str(item.get("source") or ""),
        "collection": str(item.get("collection") or ""),
        "sceneId": str(item.get("sceneId") or item.get("scene_id") or ""),
        "acquiredAt": item.get("acquiredAt"),
        "cloudCover": item.get("cloudCover", item.get("cloud_cover")),
        "resolutionM": item.get("resolutionM", item.get("resolution_m")),
    }


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
    if analysis_result is None and run.reused_result_id is not None:
        reused_row = await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.id == run.reused_result_id  # type: ignore[arg-type]
            )
        )
        analysis_result = reused_row.scalar_one_or_none()
    if analysis_result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Результат ещё не готов",
        )

    territory_rows = (
        await session.execute(
            select(ResultTerritory)
            .where(
                ResultTerritory.result_id == analysis_result.id  # type: ignore[arg-type]
            )
            .order_by(ResultTerritory.position)  # type: ignore[arg-type]
        )
    ).scalars().all()
    territory_by_id = {territory.id: territory for territory in territory_rows}
    territory_name = (
        territory_rows[0].territory_name if territory_rows else "Территория"
    )

    zone_rows: list[Any] = []
    if territory_by_id:
        zone_rows = list(
            (
                await session.execute(
                    select(  # type: ignore[call-overload]
                        ChangeZone.change_class,
                        func.ST_AsGeoJSON(
                            func.ST_Multi(func.ST_Union(ChangeZone.geom))
                        ).label("geojson"),
                        func.coalesce(func.sum(ChangeZone.area_km2), 0).label(
                            "area_km2"
                        ),
                        func.count().label("zones_count"),
                    )
                    .where(
                        ChangeZone.result_territory_id.in_(  # type: ignore[attr-defined]
                            list(territory_by_id.keys())
                        )
                    )
                    .group_by(ChangeZone.change_class)
                )
            ).all()
        )

    aggregate_stats: dict[str, dict[str, Any]] = {
        "retained": {"areaKm2": 0.0, "zonesCount": 0},
        "lost": {"areaKm2": 0.0, "zonesCount": 0},
        "new": {"areaKm2": 0.0, "zonesCount": 0},
    }
    territories: list[dict[str, Any]] = []
    for row in zone_rows:
        geojson_text = row.geojson
        if not isinstance(geojson_text, str):
            continue
        try:
            geometry = json.loads(geojson_text)
        except json.JSONDecodeError:
            continue

        change_class = str(row.change_class)
        area_km2 = float(row.area_km2 or 0)
        zones_count = int(row.zones_count or 0)
        if change_class in aggregate_stats:
            aggregate_stats[change_class]["areaKm2"] += area_km2
            aggregate_stats[change_class]["zonesCount"] += zones_count
        if len(territories) >= 5:
            continue

        stats: dict[str, dict[str, Any]] = {
            "retained": {"areaKm2": 0.0, "zonesCount": 0},
            "lost": {"areaKm2": 0.0, "zonesCount": 0},
            "new": {"areaKm2": 0.0, "zonesCount": 0},
        }
        if change_class in stats:
            stats[change_class] = {
                "areaKm2": area_km2,
                "zonesCount": zones_count,
            }
        territories.append(
            {
                "position": len(territories) + 1,
                "territoryName": territory_name,
                "aoiType": "admin",
                "boundary": geometry,
                "stats": stats,
                "sourceRunId": str(runId),
            }
        )

    sources_summary = (
        analysis_result.sources_summary
        if isinstance(analysis_result.sources_summary, dict)
        else {}
    )
    raw_sources = sources_summary.get("items")
    if not isinstance(raw_sources, list):
        datasets = sources_summary.get("datasets")
        raw_sources = datasets.get("accepted") if isinstance(datasets, dict) else []
    sources = (
        [_source_dict(item) for item in raw_sources if isinstance(item, dict)]
        if isinstance(raw_sources, list)
        else []
    )

    citations_json = (
        analysis_result.citations
        if isinstance(analysis_result.citations, dict)
        else {}
    )
    raw_citations = citations_json.get("items")
    citations = (
        [item for item in raw_citations if isinstance(item, dict)]
        if isinstance(raw_citations, list)
        else []
    )

    total_area = (
        analysis_result.total_area_km2
        if isinstance(analysis_result.total_area_km2, dict)
        else {}
    )
    raw_diagnostics = sources_summary.get("diagnostics")
    diagnostics: dict[str, Any] = {}
    if isinstance(raw_diagnostics, dict):
        diagnostics = {str(key): value for key, value in raw_diagnostics.items()}
    raw_bbox = diagnostics.get("bbox")
    bbox = (
        [float(value) for value in raw_bbox]
        if isinstance(raw_bbox, list) and len(raw_bbox) == 4
        else None
    )
    coverage = (
        diagnostics.get("coverage")
        if isinstance(diagnostics.get("coverage"), dict)
        else {}
    )
    raw_limitations = diagnostics.get("limitations")
    quality_limitations = (
        [str(item) for item in raw_limitations]
        if isinstance(raw_limitations, list)
        else []
    )
    aggregate = {
        "retained": aggregate_stats["retained"],
        "lost": aggregate_stats["lost"],
        "new": aggregate_stats["new"],
        "note": str(
            total_area.get("note")
            or "сумма по территориям (с возможным пересечением)"
        ),
    }

    return AnalysisResultSchema(
        id=analysis_result.id,
        runId=runId,
        mode=analysis_result.mode,
        territoriesCount=(
            len(territories) if territories else analysis_result.territories_count
        ),
        hasOverlap=analysis_result.has_overlap,
        crs=analysis_result.crs,
        summaryText=analysis_result.summary_text,
        explanationText=analysis_result.explanation_text,
        sources=sources,
        citations=citations,
        validationOutcome=analysis_result.validation_outcome,
        territories=territories,
        aggregate=aggregate,
        bbox=bbox,
        coverage=coverage,
        diagnostics=diagnostics,
        qualityLimitations=quality_limitations,
        createdAt=analysis_result.created_at,
    )