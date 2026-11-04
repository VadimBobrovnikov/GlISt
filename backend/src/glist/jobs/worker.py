"""ARQ воркеры: конфигурация и классы задач (задача T033).

Настройка ARQ с max_jobs=1 — одновременно выполняется только один анализ.
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from arq.worker import Worker as ArqWorker

from glist.config import get_settings
from glist.jobs.analyze_run import analyze_run


class WorkerSettings:  # pylint: disable=too-few-public-methods
    """Настройки ARQ-воркера: функции задач и параметры соединения с Redis."""

    functions = [analyze_run]
    max_jobs = 1  # Один анализ одновременно — предотвращает гонки за БД
    job_timeout = 3600  # 1 час на выполнение одного запуска

    @staticmethod
    async def startup(ctx: dict[str, Any]) -> None:  # noqa: ANN001
        """Инициализация при запуске воркера."""
        settings = get_settings()
        ctx["redis_settings"] = RedisSettings.from_dsn(
            settings.redis_url.get_secret_value()
        )

    @staticmethod
    async def shutdown(ctx: dict[str, Any]) -> None:  # noqa: ANN001
        """Очистка при остановке воркера."""
        pass


def create_worker() -> ArqWorker:
    """Создаёт экземпляр ARQ-воркера с настройками по умолчанию."""
    return ArqWorker(functions=[analyze_run])