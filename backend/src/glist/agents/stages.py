"""Логирование этапов работы агентов в таблицу run_stage (задача T032).

Вспомогательные функции для записи этапов (researcher, analyst, visualizer,
validator) в таблицу run_stage с соблюдением Принципа III:
description не может быть пустым.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import RunStage


async def log_run_stage(
    session: AsyncSession,
    *,
    run_id: UUID,
    agent: str,
    stage_code: str,
    title: str,
    description: str,
    status: str,
    attempt_no: int = 0,
    details: dict[str, Any] | None = None,
) -> RunStage:
    """Записывает этап работы агента в таблицу run_stage.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска analysis_run.
        agent: Имя агента (researcher/analyst/visualizer/validator).
        stage_code: Код этапа (например, 'data_collection').
        title: Краткое название этапа.
        description: Подробное описание (обязательно, не пустое — Принцип III).
        status: Статус этапа (running/completed/failed/skipped).
        attempt_no: Номер попытки (по умолчанию 0).
        details: Дополнительные детали в формате JSONB.

    Returns:
        Созданный объект RunStage.

    Raises:
        ValueError: если description пустой.
    """
    if not description or not description.strip():
        raise ValueError("description не может быть пустым (Принцип III)")

    # Вычисляем seq как максимальный существующий + 1
    result = await session.execute(
        select(RunStage).where(RunStage.run_id == run_id)  # type: ignore[arg-type]
    )
    existing_stages = result.scalars().all()
    existing_seqs = [s.seq for s in existing_stages if s.seq is not None]
    next_seq = max(existing_seqs) + 1 if existing_seqs else 1

    stage = RunStage(
        run_id=run_id,
        seq=next_seq,
        agent=agent,
        stage_code=stage_code,
        title=title,
        description=description,
        status=status,
        attempt_no=attempt_no,
        details=details,
        started_at=datetime.now(UTC) if status == "running" else None,
        finished_at=datetime.now(UTC)
        if status in ("completed", "failed", "skipped")
        else None,
    )

    session.add(stage)
    await session.flush()
    await session.refresh(stage)
    return stage


async def update_run_stage(
    session: AsyncSession,
    stage_id: UUID,
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> RunStage | None:
    """Обновляет статус существующего этапа run_stage.

    Args:
        session: Асинхронная сессия БД.
        stage_id: Идентификатор этапа RunStage.
        status: Новый статус (running/completed/failed/skipped).
        details: Дополнительные детали (опционально).

    Returns:
        Обновлённый объект RunStage или None, если этап не найден.
    """
    result = await session.execute(select(RunStage).where(RunStage.id == stage_id))  # type: ignore[arg-type]
    stage = result.scalar_one_or_none()
    if stage is None:
        return None

    stage.status = status
    if details is not None:
        stage.details = details
    if status in ("completed", "failed", "skipped"):
        stage.finished_at = datetime.now(UTC)

    await session.flush()
    await session.refresh(stage)
    return stage