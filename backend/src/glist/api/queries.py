"""Роутер запросов на естественном языке (задача T063).

Endpoints:
- ``POST /api/v1/queries`` — создание запроса, запись Query и AnalysisRun,
  постановка задачи ``analyze_run`` в ARQ-очередь.
- ``GET /api/v1/queries`` — история запросов с курсорной пагинацией.
- ``GET /api/v1/queries/{queryId}`` — карточка запроса.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.api.deps import get_current_user, get_session
from glist.config import Settings, get_settings
from glist.db.models import AnalysisRun, Query, SettingsProfile, User
from glist.services.queue_service import enqueue_run
from glist.services.run_service import STATUS_WAITING
from glist.tools.geocode import geocode_territory

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


class QueryCreate(BaseModel):
    """Входные данные для создания запроса (схема QueryCreate из контракта)."""

    text: str = Field(min_length=3, max_length=1000)
    parentQueryId: uuid.UUID | None = None
    mode: str | None = None
    territory: dict[str, Any] | None = None


class TerritoryResolveRequest(BaseModel):
    """Запрос предварительного выбора территории (район + город)."""

    text: str = Field(min_length=3, max_length=1000)
    name: str | None = None
    parent: str | None = None
    type: str | None = None


class RunSummary(BaseModel):
    id: uuid.UUID
    status: str
    createdAt: datetime


class QuerySummary(BaseModel):
    id: uuid.UUID
    text: str
    status: str
    createdAt: datetime
    lastRunId: uuid.UUID | None = None


class QueryCreated(BaseModel):
    """Ответ POST /api/v1/queries (схема QueryCreated из контракта)."""

    query: QuerySummary
    run: RunSummary
    clarification: dict[str, Any] | None = None


async def _ensure_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    current_user: dict[str, Any],
) -> None:
    """Создаёт локального пользователя, если его ещё нет (FK query.user_id)."""
    existing = await session.get(User, user_id)
    if existing is not None:
        return
    display_name = str(current_user.get("display_name") or "Локальный пользователь")
    session.add(User(id=user_id, display_name=display_name))
    await session.flush()


async def _ensure_settings_profile(
    session: AsyncSession,
    settings_hash: str,
    settings: Settings,
) -> None:
    """Создаёт профиль настроек, если его ещё нет (FK analysis_run.settings_hash)."""
    existing = await session.get(SettingsProfile, settings_hash)
    if existing is not None:
        return
    session.add(
        SettingsProfile(
            settings_hash=settings_hash,
            name="default",
            values={
                "llm_provider": settings.llm_provider,
                "llm_model": settings.llm_model,
                "embedding_model": settings.embedding_model,
                "seed": settings.seed,
                "ndvi_threshold": 0.25,
                "min_area_m2": 1000,
            },
        )
    )
    await session.flush()



@router.post("/territories/resolve", tags=["queries"])
async def resolve_territory(  # noqa: B008
    body: TerritoryResolveRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Предварительно выбирает объект до запуска анализа.

    Для района возвращает только объект внутри указанного города; если
    граница не найдена или объектов несколько — статус и кандидаты.
    """
    from glist.agents.researcher import extract_territory_params

    if body.name:
        requested: dict[str, Any] = {
            "name": body.name.strip(),
            "normalizedName": body.name.strip().lower(),
            "parent": body.parent.strip() if body.parent else None,
            "type": body.type or ("district" if body.parent else "city"),
        }
    else:
        extracted = await extract_territory_params(body.text)
        if extracted is None:
            return {
                "status": "extraction_failed",
                "reason": "extraction_failed",
                "message": (
                    "Не удалось извлечь название территории из запроса. "
                    "Уточните запрос или выделите участок на карте."
                ),
                "requested": {"name": None, "parent": None, "type": None},
                "candidates": [],
            }
        requested = dict(extracted)

    try:
        payload = await geocode_territory(
            str(requested.get("name") or ""),
            parent=requested.get("parent"),
            object_type=requested.get("type"),
        )
    except Exception as exc:  # noqa: BLE001 — сбой геокодера ≠ «объект не найден»
        logger.warning("Territory resolve failed: %s", exc)
        return {
            "status": "geocoder_error",
            "reason": "geocoder_error",
            "message": (
                "Сервис геокодирования недоступен или ограничил запросы "
                f"({exc}). Можно выбрать участок на карте вручную."
            ),
            "requested": requested,
            "candidates": [],
        }
    if payload is None:
        name = str(requested.get("name") or "")
        parent = requested.get("parent")
        if not name.strip() or name == "Территория запроса":
            message = (
                "Не удалось извлечь название города из запроса. Уточните "
                "запрос или выберите участок на карте."
            )
            reason = "empty_territory"
        elif parent:
            message = f"Район «{name}» в «{parent}» не найден."
            reason = "district_not_found"
        else:
            message = f"Объект «{name}» не найден."
            reason = "not_found"
        return {
            "status": "not_found",
            "reason": reason,
            "message": message,
            "requested": requested,
            "candidates": [],
        }
    candidates = [
        {
            "name": item.get("name"),
            "displayName": item.get("displayName"),
            "kind": item.get("kind"),
            "city": item.get("city"),
            "parent": item.get("parent"),
            "addresstype": item.get("addresstype"),
            "bbox": item.get("bbox"),
            "geometry": item.get("geometry"),
            "pointOnly": item.get("pointOnly"),
        }
        for item in (payload.get("candidates") or [])
        if isinstance(item, dict) and item.get("bbox")
    ]
    selected = {
        "name": payload.get("name"),
        "displayName": payload.get("displayName"),
        "kind": payload.get("kind"),
        "city": payload.get("city"),
        "addresstype": payload.get("addresstype"),
        "bbox": payload.get("bbox"),
        "geometry": payload.get("geometry"),
        "parent": requested.get("parent"),
        "type": requested.get("type") or "place",
        "pointOnly": payload.get("pointOnly"),
        "source": "geocoding",
    }
    if not isinstance(selected.get("geometry"), dict):
        status = "point_only" if selected.get("pointOnly") else "boundary_missing"
        return {
            "status": status,
            "reason": status,
            "message": (
                f"У объекта «{selected.get('name')}» найдена только точка, "
                "границы нет. Можно выбрать участок на карте вручную."
                if status == "point_only"
                else f"У объекта «{selected.get('name')}» не найдена граница. "
                "Можно выбрать участок на карте вручную."
            ),
            "requested": requested,
            "selected": selected,
            "candidates": candidates,
        }
    status = "ambiguous" if payload.get("ambiguous") else "resolved"
    return {
        "status": status,
        "reason": "ambiguous" if status == "ambiguous" else "ok",
        "message": None,
        "requested": requested,
        "selected": selected,
        "candidates": candidates,
    }


@router.post(
    "/queries",
    status_code=status.HTTP_201_CREATED,
    response_model=QueryCreated,
    tags=["queries"],
)
async def create_query(  # noqa: B008
    body: QueryCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> QueryCreated:
    """Создаёт запрос, AnalysisRun и ставит задачу в ARQ-очередь."""
    user_id = uuid.UUID(current_user["id"])
    query_text = body.text.strip()
    settings = get_settings()
    settings_hash = "default_settings_hash"

    # Заглушка аутентификации и профиль настроек должны существовать до вставки
    # query/analysis_run: на них ссылаются FK.
    await _ensure_user(session, user_id, current_user)
    await _ensure_settings_profile(session, settings_hash, settings)

    query = Query(
        user_id=user_id,
        text=query_text,
        language="ru",
        query_kind="follow_up" if body.parentQueryId else "primary",
        parent_query_id=body.parentQueryId,
        status=STATUS_WAITING,
        extracted=(
            {"territory": body.territory} if body.territory is not None else None
        ),
    )
    session.add(query)
    await session.flush()

    run_id = uuid.uuid4()
    trace_id = uuid.uuid4().hex
    params_hash_input = f"{query_text}:{body.parentQueryId or ''}:{body.mode or ''}"
    params_hash = hashlib.sha256(params_hash_input.encode()).hexdigest()

    run = AnalysisRun(
        id=run_id,
        query_id=query.id,
        parent_run_id=None,
        status=STATUS_WAITING,
        trace_id=trace_id,
        settings_hash=settings_hash,
        params_hash=params_hash,
        seed=settings.seed,
        rag_index_version="v1",
        model_versions={
            "llmModel": settings.llm_model,
            "embeddingModel": settings.embedding_model,
            "promptVersions": ["v1", "v1", "v1", "v1"],
        },
    )
    session.add(run)
    await session.flush()

    await enqueue_run(session, run_id=run.id, query_id=str(query.id))
    # get_session не коммитит автоматически: фиксируем запрос и запуск до
    # постановки задачи в ARQ, иначе воркер и GET /runs запись не увидят.
    await session.commit()

    redis_url = settings.redis_url.get_secret_value()
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    await pool.enqueue_job("analyze_run", str(run.id))
    await pool.close()

    return QueryCreated(
        query=QuerySummary(
            id=query.id,
            text=query.text,
            status=query.status,
            createdAt=query.created_at,
            lastRunId=run.id,
        ),
        run=RunSummary(
            id=run.id,
            status=run.status,
            createdAt=run.created_at,
        ),
    )


@router.get("/queries", tags=["queries"])
async def list_queries(  # noqa: B008
    limit: int = 20,
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Возвращает страницу истории запросов пользователя."""
    user_id = uuid.UUID(current_user["id"])
    result = await session.execute(
        select(Query)
        .where(Query.user_id == user_id)  # type: ignore[arg-type]
        .order_by(desc(Query.created_at))  # type: ignore[arg-type]
        .limit(limit)
    )
    items = result.scalars().all()
    history: list[dict[str, Any]] = []
    for query in items:
        run_result = await session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.query_id == query.id)  # type: ignore[arg-type]
            .order_by(desc(AnalysisRun.created_at))  # type: ignore[arg-type]
            .limit(1)
        )
        last_run = run_result.scalar_one_or_none()
        history.append(
            {
                "id": query.id,
                "text": query.text,
                "status": last_run.status if last_run is not None else query.status,
                "createdAt": query.created_at,
                "lastRunId": last_run.id if last_run is not None else None,
            }
        )
    return {"items": history}


@router.get("/queries/{queryId}", tags=["queries"])
async def get_query(  # noqa: B008
    queryId: uuid.UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Возвращает карточку запроса по идентификатору."""
    user_id = uuid.UUID(current_user["id"])
    result = await session.execute(
        select(Query).where(
            Query.id == queryId,  # type: ignore[arg-type]
            Query.user_id == user_id,  # type: ignore[arg-type]
        )
    )
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    run_result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.query_id == query.id)  # type: ignore[arg-type]
        .order_by(desc(AnalysisRun.created_at))  # type: ignore[arg-type]
        .limit(1)
    )
    last_run = run_result.scalar_one_or_none()
    return {
        "id": query.id,
        "text": query.text,
        "status": last_run.status if last_run is not None else query.status,
        "createdAt": query.created_at,
        "lastRunId": last_run.id if last_run is not None else None,
    }