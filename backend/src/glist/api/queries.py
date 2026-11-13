"""Роутер запросов на естественном языке (задача T063).

Endpoints:
- ``POST /api/v1/queries`` — создание запроса, запись Query и AnalysisRun,
  постановка задачи ``analyze_run`` в ARQ-очередь.
- ``GET /api/v1/queries`` — история запросов с курсорной пагинацией.
- ``GET /api/v1/queries/{queryId}`` — карточка запроса.
"""

from __future__ import annotations

import hashlib
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

router = APIRouter(prefix="/api/v1")


class QueryCreate(BaseModel):
    """Входные данные для создания запроса (схема QueryCreate из контракта)."""

    text: str = Field(min_length=3, max_length=1000)
    parentQueryId: uuid.UUID | None = None
    mode: str | None = None


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
    return {
        "items": [
            {
                "id": q.id,
                "text": q.text,
                "status": q.status,
                "createdAt": q.created_at,
                "lastRunId": None,
            }
            for q in items
        ]
    }


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
    return {
        "id": query.id,
        "text": query.text,
        "status": query.status,
        "createdAt": query.created_at,
    }