"""Unit-тесты моделей БД (задача T013).

Проверяют CHECK-ограничения, внешние ключи и индексы моделей
user, query, clarification_request, analysis_run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.inspection import inspect
from sqlalchemy.pool import NullPool

from glist.db.models import (
    AnalysisRun,
    ClarificationRequest,
    Query,
    User,
)


async def _create_tables(engine: AsyncEngine) -> None:
    """Создаёт таблицы user, query, clarification_request.

    analysis_run не создаётся: ссылается на analysis_result.id и
    settings_profile.settings_hash, которые появятся в T014/T016.
    """
    async with engine.begin() as connection:
        await connection.run_sync(cast(Any, User).__table__.create)
        await connection.run_sync(cast(Any, Query).__table__.create)
        await connection.run_sync(cast(Any, ClarificationRequest).__table__.create)


async def _drop_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(cast(Any, ClarificationRequest).__table__.drop)
        await connection.run_sync(cast(Any, Query).__table__.drop)
        await connection.run_sync(cast(Any, User).__table__.drop)


@pytest.fixture(scope="session")
async def schema(database_url: str) -> AsyncIterator[None]:
    """Создаёт таблицы один раз на сессию; db_session изолирует данные теста."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        await _create_tables(engine)
        yield
    finally:
        await _drop_tables(engine)
        await engine.dispose()


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


async def test_user_create_valid(db_session: AsyncSession, schema: None) -> None:
    user = User(display_name="Алексей", is_local=True)
    db_session.add(user)
    await db_session.flush()
    assert user.id is not None
    assert user.display_name == "Алексей"
    assert user.is_local is True
    assert isinstance(user.created_at, datetime)


async def test_user_defaults(db_session: AsyncSession, schema: None) -> None:
    user = User(display_name="Алексей")
    db_session.add(user)
    await db_session.flush()
    assert user.is_local is True
    assert isinstance(user.created_at, datetime)


async def test_user_display_name_empty_rejected(db_session: AsyncSession, schema: None) -> None:
    user = User(display_name="", is_local=True)
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession, display_name: str = "Алексей") -> User:
    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    return user


async def test_query_create_valid(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="Где ближайшая станция метро?",
        language="ru",
        query_kind="primary",
        status="ожидает",
    )
    db_session.add(query)
    await db_session.flush()
    assert query.id is not None
    assert query.status == "ожидает"
    assert isinstance(query.created_at, datetime)
    assert isinstance(query.updated_at, datetime)


async def test_query_all_statuses_accepted(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    statuses = (
        "ожидает",
        "в обработке",
        "завершён",
        "не прошёл проверку",
        "данные недоступны",
        "ошибка",
    )
    for status in statuses:
        query = Query(
            user_id=user.id,
            text="Тест",
            language="ru",
            query_kind="primary",
            status=status,
        )
        db_session.add(query)
        await db_session.flush()


async def test_query_invalid_status_rejected(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="Тест",
        language="ru",
        query_kind="primary",
        status="неизвестный_статус",
    )
    db_session.add(query)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_query_invalid_language_rejected(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="Тест",
        language="fr",
        query_kind="primary",
        status="ожидает",
    )
    db_session.add(query)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_query_invalid_query_kind_rejected(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="Тест",
        language="ru",
        query_kind="secondary",
        status="ожидает",
    )
    db_session.add(query)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_query_text_too_short_rejected(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="АБ",
        language="ru",
        query_kind="primary",
        status="ожидает",
    )
    db_session.add(query)


# ---------------------------------------------------------------------------
# ClarificationRequest
# ---------------------------------------------------------------------------


async def _make_query(session: AsyncSession, user: User) -> Query:
    query = Query(
        user_id=user.id,
        text="Где ближайшая станция метро?",
        language="ru",
        query_kind="primary",
        status="ожидает",
    )
    session.add(query)
    await session.flush()
    return query


async def test_clarification_request_create_valid(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = await _make_query(db_session, user)
    cr = ClarificationRequest(
        query_id=query.id,
        kind="ambiguity",
        question_text="Уточните район города?",
        options=[{"id": "a", "text": "Центр"}, {"id": "b", "text": "Север"}],
        status="ожидает ответа",
    )
    db_session.add(cr)
    await db_session.flush()
    assert cr.id is not None
    assert cr.status == "ожидает ответа"
    assert cr.answered_at is None


async def test_clarification_request_all_statuses_accepted(
    db_session: AsyncSession, schema: None
) -> None:
    user = await _make_user(db_session)
    query = await _make_query(db_session, user)
    for status in ("ожидает ответа", "отвечен", "истёк"):
        cr = ClarificationRequest(
            query_id=query.id,
            kind="ambiguity",
            question_text="Тест?",
            options=[{"id": "a", "text": "Да"}],
            status=status,
        )
        db_session.add(cr)
        await db_session.flush()


async def test_clarification_request_invalid_kind_rejected(
    db_session: AsyncSession, schema: None
) -> None:
    user = await _make_user(db_session)
    query = await _make_query(db_session, user)
    cr = ClarificationRequest(
        query_id=query.id,
        kind="unknown_kind",
        question_text="Тест?",
        options=[{"id": "a", "text": "Да"}],
        status="ожидает ответа",
    )
    db_session.add(cr)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_clarification_request_invalid_status_rejected(
    db_session: AsyncSession, schema: None
) -> None:
    user = await _make_user(db_session)
    query = await _make_query(db_session, user)
    cr = ClarificationRequest(
        query_id=query.id,
        kind="ambiguity",
        question_text="Тест?",
        options=[{"id": "a", "text": "Да"}],
        status="ожидает",
    )
    db_session.add(cr)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# AnalysisRun — проверка определений ограничений (без вставки, т.к. ссылается
# на analysis_result / settings_profile, которые появятся в T014/T016)
# ---------------------------------------------------------------------------


def test_analysis_run_status_check_defined() -> None:
    names = {
        c.name
        for c in cast(Any, AnalysisRun).__table__.constraints
        if getattr(c, "name", None)
    }
    assert "analysis_run_status_check" in names


def test_analysis_run_attempt_count_check_defined() -> None:
    names = {
        c.name
        for c in cast(Any, AnalysisRun).__table__.constraints
        if getattr(c, "name", None)
    }
    assert "analysis_run_attempt_count_check" in names


def test_analysis_run_rejection_category_check_defined() -> None:
    names = {
        c.name
        for c in cast(Any, AnalysisRun).__table__.constraints
        if getattr(c, "name", None)
    }
    assert "analysis_run_rejection_category_check" in names


def test_analysis_run_trace_id_not_null() -> None:
    mapper = inspect(AnalysisRun)
    column = mapper.columns["trace_id"]
    assert column.nullable is False


def test_analysis_run_attempt_count_not_null() -> None:
    mapper = inspect(AnalysisRun)
    column = mapper.columns["attempt_count"]
    assert column.nullable is False


def test_analysis_run_indexes_defined() -> None:
    mapper = cast(Any, inspect(AnalysisRun))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_analysis_run_status_queue_position" in index_names
    assert "ix_analysis_run_query_created_at" in index_names
    assert "ix_analysis_run_params_hash" in index_names


def test_analysis_run_partial_indexes_defined() -> None:
    mapper = cast(Any, inspect(AnalysisRun))
    indexes = list(mapper.mapped_table.indexes)
    ref_index = next(ix for ix in indexes if ix.name == "ix_analysis_run_is_reference")
    assert ref_index is not None
    retention_index = next(ix for ix in indexes if ix.name == "ix_analysis_run_retention_until")
    assert retention_index is not None


def test_analysis_run_fk_self_reference() -> None:
    mapper = inspect(AnalysisRun)
    parent_col = mapper.columns["parent_run_id"]
    assert any(fk.column.table.name == "analysis_run" for fk in parent_col.foreign_keys)


def test_analysis_run_fk_reused_result() -> None:
    mapper = inspect(AnalysisRun)
    reused_col = mapper.columns["reused_result_id"]
    assert any("analysis_result" in str(fk.target_fullname) for fk in reused_col.foreign_keys)


def test_analysis_run_fk_settings_profile() -> None:
    mapper = inspect(AnalysisRun)
    settings_col = mapper.columns["settings_hash"]
    assert any("settings_profile" in str(fk.target_fullname) for fk in settings_col.foreign_keys)


def test_analysis_run_all_statuses_in_check() -> None:
    """Проверяет, что CHECK содержит все 6 статуса из спецификации."""
    constraints = [
        c
        for c in cast(Any, AnalysisRun).__table__.constraints
        if getattr(c, "name", None) == "analysis_run_status_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    statuses = (
        "ожидает",
        "в обработке",
        "завершён",
        "не прошёл проверку",
        "данные недоступны",
        "ошибка",
    )
    for status in statuses:
        assert status in sql


async def test_query_text_too_long_rejected(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    query = Query(
        user_id=user.id,
        text="x" * 1001,
        language="ru",
        query_kind="primary",
        status="ожидает",
    )
    db_session.add(query)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_query_parent_query_self_reference(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    parent = Query(
        user_id=user.id,
        text="Первый запрос",
        language="ru",
        query_kind="primary",
        status="ожидает",
    )
    db_session.add(parent)
    await db_session.flush()

    child = Query(
        user_id=user.id,
        text="Уточнение",
        language="ru",
        query_kind="follow_up",
        parent_query_id=parent.id,
        status="ожидает",
    )
    db_session.add(child)
    await db_session.flush()
    assert child.parent_query_id == parent.id

