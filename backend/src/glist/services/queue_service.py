"""Сервис очереди запусков (задача T034).

Обеспечивает постановку запуска в очередь, получение позиции в очереди и
автостарт следующего запуска после завершения текущего.

Гарантирует, что в БД только один запуск имеет статус «в обработке»,
остальные — «ожидает» с queue_position.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import AnalysisRun

# Статусы, совместимые с CHECK-ограничением в модели AnalysisRun
STATUS_WAITING = "ожидает"
STATUS_PROCESSING = "в обработке"
STATUS_COMPLETED = "завершён"
STATUS_FAILED_VALIDATION = "не прошёл проверку"
STATUS_NO_DATA = "данные недоступны"
STATUS_ERROR = "ошибка"


async def enqueue_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    query_id: str,
) -> int:
    """Поставляет запуск в очередь.

    Назначает статус «ожидает» и вычисляет queue_position как
    максимальный текущий queue_position + 1 (или 1, если очередь пуста).

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска.
        query_id: Идентификатор запроса.

    Returns:
        Позиция в очереди (начиная с 1).
    """
    # Получаем все запуски в очереди и вычисляем максимальную позицию
    result = await session.execute(
        select(AnalysisRun).where(
            AnalysisRun.status == STATUS_WAITING  # type: ignore[arg-type]
        )
    )
    waiting_runs = result.scalars().all()
    positions = [
        r.queue_position for r in waiting_runs if r.queue_position is not None
    ]
    queue_position = max(positions) + 1 if positions else 1

    await session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
        .values(
            status=STATUS_WAITING,
            queue_position=queue_position,
            queued_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
        )
    )
    await session.flush()
    return queue_position


async def get_queue_position(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> int | None:
    """Возвращает текущую позицию запуска в очереди.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска.

    Returns:
        Позиция в очереди или None, если запуск не найден или не в очереди.
    """
    result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
    )
    run = result.scalar_one_or_none()
    return run.queue_position if run else None


async def get_queue_length(session: AsyncSession) -> int:
    """Возвращает количество запусков со статусом «ожидает».

    Args:
        session: Асинхронная сессия БД.

    Returns:
        Количество запусков в очереди.
    """
    result = await session.execute(
        select(AnalysisRun).where(
            AnalysisRun.status == STATUS_WAITING  # type: ignore[arg-type]
        )
    )
    return len(result.scalars().all())


async def claim_next_run(
    session: AsyncSession,
) -> UUID | None:
    """Забирает следующий запуск из очереди для обработки.

    Устанавливает статус «в обработке», обнуляет queue_position,
    фиксирует started_at. Гарантирует, что только один запуск
    имеет статус «в обработке» в каждый момент времени.

    Args:
        session: Асинхронная сессия БД.

    Returns:
        Идентификатор взятого запуска или None, если очередь пуста.
    """
    # Берём первый в очереди (самый старый по queued_at)
    result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.status == STATUS_WAITING)  # type: ignore[arg-type]
        .order_by(AnalysisRun.queued_at.asc().nullslast())  # type: ignore[union-attr]
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None

    run_id = run.id
    run.status = STATUS_PROCESSING
    run.queue_position = None
    run.started_at = datetime.now(UTC)

    await session.flush()
    return run_id


async def complete_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    status: str = STATUS_COMPLETED,
) -> None:
    """Завершает запуск и запускает следующий из очереди (автостарт).

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор завершённого запуска.
        status: Финальный статус (завершён / не прошёл проверку / данные недоступны / ошибка).
    """
    await session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
        .values(
            status=status,
            finished_at=datetime.now(UTC),
        )
    )
    await session.flush()

    # Автостарт следующего запуска
    await claim_next_run(session)