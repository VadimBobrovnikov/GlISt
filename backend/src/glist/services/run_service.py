"""Сервис жизненного цикла запуска (задача T035).

Функции для смены статусов запуска и проверки идемпотентности по params_hash.

Статусы (совпадают с CHECK-ограничением в модели AnalysisRun):
    ожидает, в обработке, завершён, не прошёл проверку, данные недоступны, ошибка.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import AnalysisRun

# Статусы, совместимые с CHECK-ограничением
STATUS_WAITING = "ожидает"
STATUS_PROCESSING = "в обработке"
STATUS_COMPLETED = "завершён"
STATUS_FAILED_VALIDATION = "не прошёл проверку"
STATUS_NO_DATA = "данные недоступны"
STATUS_ERROR = "ошибка"

VALID_STATUSES = frozenset(
    {
        STATUS_WAITING,
        STATUS_PROCESSING,
        STATUS_COMPLETED,
        STATUS_FAILED_VALIDATION,
        STATUS_NO_DATA,
        STATUS_ERROR,
    }
)


def _validate_status(status: str) -> None:
    """Проверяет, что статус допустим."""
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Недопустимый статус '{status}'. Допустимые: {', '.join(sorted(VALID_STATUSES))}"
        )


async def set_run_status(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    status: str,
    status_reason: str | None = None,
) -> None:
    """Устанавливает статус запуска.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска.
        status: Новый статус (один из VALID_STATUSES).
        status_reason: Причина изменения статуса (опционально).
    """
    _validate_status(status)

    await session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
        .values(
            status=status,
            status_reason=status_reason,
            finished_at=datetime.now(UTC)
            if status
            in (STATUS_COMPLETED, STATUS_FAILED_VALIDATION, STATUS_NO_DATA, STATUS_ERROR)
            else None,
        )
    )
    await session.flush()


async def get_run(
    session: AsyncSession,
    *,
    run_id: str | UUID,
) -> AnalysisRun | None:
    """Возвращает объект AnalysisRun по идентификатору.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска.

    Returns:
        Объект AnalysisRun или None.
    """
    result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
    )
    return result.scalar_one_or_none()


async def find_completed_run_by_params_hash(
    session: AsyncSession,
    *,
    params_hash: str,
) -> AnalysisRun | None:
    """Ищет завершённый запуск с таким же params_hash (идемпотентность).

    Если такой запуск найден, новый запуск не создаётся — вместо него
    используется предыдущий результат через reused_result_id.

    Args:
        session: Асинхронная сессия БД.
        params_hash: Хэш параметров запроса.

    Returns:
        Завершённый AnalysisRun или None.
    """
    result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.params_hash == params_hash)  # type: ignore[arg-type]
        .where(AnalysisRun.status == STATUS_COMPLETED)  # type: ignore[arg-type]
        .order_by(AnalysisRun.finished_at.desc().nullslast())  # type: ignore[union-attr]
        .limit(1)
    )
    return result.scalar_one_or_none()


async def link_reused_result(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    reused_result_id: str | UUID,
) -> None:
    """Привязывает reused_result_id к запуску (идемпотентный повтор).

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор нового запуска.
        reused_result_id: Идентификатор предыдущего результата analysis_result.
    """
    await session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)  # type: ignore[arg-type]
        .values(reused_result_id=reused_result_id)
    )
    await session.flush()


async def get_reused_result(
    session: AsyncSession,
    *,
    run_id: str | UUID,
) -> UUID | None:
    """Возвращает reused_result_id для запуска, если он привязан к предыдущему результату.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска.

    Returns:
        Идентификатор analysis_result или None.
    """
    run = await get_run(session, run_id=run_id)
    return run.reused_result_id if run else None