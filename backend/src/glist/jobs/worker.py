"""ARQ воркеры: конфигурация и классы задач (задача T033).

Настройка ARQ с max_jobs=1 — одновременно выполняется только один анализ.
"""

from __future__ import annotations

import os

from arq.connections import RedisSettings
from arq.worker import Worker as ArqWorker

from glist.jobs.analyze_run import analyze_run


def _redis_settings_from_env() -> RedisSettings:
    """Возвращает параметры Redis для ARQ из ``REDIS_URL``.

    ARQ CLI читает атрибут ``redis_settings`` у класса ``WorkerSettings``.
    Без него воркер подключается к ``localhost:6379`` и не видит сервис
    ``redis`` в docker-сети. В docker-compose.yml ``REDIS_URL`` уже перекрыт
    на ``redis://:...@redis:6379/0``.
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        return RedisSettings.from_dsn(url)
    return RedisSettings()


class WorkerSettings:  # pylint: disable=too-few-public-methods
    """Настройки ARQ-воркера: функции задач и параметры соединения с Redis."""

    functions = [analyze_run]
    redis_settings = _redis_settings_from_env()
    max_jobs = 1  # Один анализ одновременно — предотвращает гонки за БД
    job_timeout = 3600  # 1 час на выполнение одного запуска


def create_worker() -> ArqWorker:
    """Создаёт экземпляр ARQ-воркера с настройками по умолчанию."""
    return ArqWorker(functions=[analyze_run])