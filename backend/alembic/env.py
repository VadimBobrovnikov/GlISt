"""Окружение Alembic для GlISt (T007).

- async engine (asyncpg): соответствует async-доступу к БД (T012) и CI-гейту
  ``alembic upgrade head && alembic check``;
- URL берётся из переменной DATABASE_URL (окружение или backend/.env),
  в ini-файле секреты не хранятся (Принцип IX);
- ``target_metadata`` — из ``glist.db.models`` (создаётся в T013–T017);
  до появления модуля любая команда, требующая моделей, завершается
  ошибкой: autogenerate без моделей опасен (может сгенерировать удаление
  таблиц). Для ``alembic history``/``heads`` env.py не выполняется.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config

from alembic import context

# backend/ — каталог, где лежит alembic.ini; src/ добавляем в sys.path
# независимо от текущей рабочей директории, чтобы импортировать glist.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _BACKEND_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Импорт модуля моделей регистрирует таблицы в общем SQLModel.metadata (T013+).
try:
    from sqlmodel import SQLModel

    from glist.db import models as _models  # noqa: F401 — регистрация таблиц

    target_metadata: MetaData = SQLModel.metadata
except ImportError:
    # В Phase 1 модуля ещё нет. Пустая MetaData опасна: autogenerate может
    # сгенерировать миграцию на удаление всех таблиц — поэтому ошибка.
    raise RuntimeError(
        "glist.db.models не найден. Autogenerate/migrate требуют "
        "моделей (T013–T017). В Phase 1 это ожидаемо — не запускайте "
        "autogenerate/upgrade до появления моделей."
    ) from None


def _database_url() -> str | None:
    """Возвращает DATABASE_URL из окружения или backend/.env (или None)."""
    url = os.environ.get("DATABASE_URL")
    if url is None:
        env_file = _BACKEND_ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("DATABASE_URL="):
                    url = stripped.partition("=")[2].strip().strip("\"'")
                    break
    if url and url.startswith("postgresql://"):
        # Явный драйвер для async engine (asyncpg).
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


def run_migrations_offline() -> None:
    """Оффлайн-режим (``alembic upgrade head --sql``): SQL без подключения."""
    url = _database_url()
    if url is None:
        raise RuntimeError("DATABASE_URL не задан: окружение или backend/.env (см. .env.example)")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


# PostGIS extension tables that exist in the DB but are not part of our metadata.
# These should never appear in autogenerate diffs.
_POSTGIS_SYSTEM_TABLES = frozenset({
    "spatial_ref_sys",
    "layer",
    "topology",
    "topology_config",
})


def include_object(
    object: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any | None,
) -> bool:
    """Exclude PostGIS system tables from autogenerate comparison (T017)."""
    return not (type_ == "table" and name in _POSTGIS_SYSTEM_TABLES)


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Асинхронное подключение: единый путь для upgrade/check/revision."""
    section = config.get_section(config.config_ini_section)
    if section is None:  # pragma: no cover — секция [alembic] существует в ini
        raise RuntimeError("Секция alembic не найдена в alembic.ini")
    connectable: AsyncEngine = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Онлайн-режим: подключение к БД и выполнение миграций."""
    url = _database_url()
    if url is None:
        raise RuntimeError(
            "DATABASE_URL не задан: экспортируйте переменную или создайте backend/.env "
            "(см. .env.example); локально БД поднимается через docker compose up -d postgres"
        )
    config.set_main_option("sqlalchemy.url", url)
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
