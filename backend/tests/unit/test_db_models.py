"""Unit-тесты моделей БД (задача T016).

Проверяют CHECK-ограничения, внешние ключи и индексы моделей
user, query, clarification_request, analysis_run, run_stage,
validation_rejection, settings_profile, geocode_cache,
area_of_interest, observation_dataset, run_dataset,
analysis_result, result_territory, change_zone.
"""

from __future__ import annotations

import decimal
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.inspection import inspect
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from glist.db.models import (
    AnalysisRun,
    AreaOfInterest,
    Artifact,
    ArtifactDeletionLog,
    ClarificationRequest,
    GeocodeCache,
    KnowledgeChunk,
    KnowledgeDocument,
    ObservationDataset,
    Query,
    RunDataset,
    RunStage,
    SettingsProfile,
    User,
    ValidationRejection,
)


async def _create_tables(engine: AsyncEngine) -> None:
    """Создаёт в тестовой БД все таблицы моделей SQLModel.

    Порядок таблиц и внешних ключей определяет сам SQLAlchemy по зависимостям.
    Цикл AnalysisRun -> AnalysisResult -> AnalysisRun разорван внешним ключом
    ``ForeignKey("analysis_result.id", use_alter=True)``: такой FK не попадает в
    ``CREATE TABLE`` и добавляется отдельным ``ALTER TABLE`` после создания
    обеих таблиц (см. ``glist.db.models.AnalysisRun.reused_result_id``).
    """
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(sync_connection, checkfirst=True)
        )


async def _drop_tables(engine: AsyncEngine) -> None:
    """Удаляет созданные таблицы (в порядке зависимостей — силами SQLAlchemy)."""
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.drop_all(sync_connection, checkfirst=True)
        )


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


async def _make_settings_profile(session: AsyncSession) -> SettingsProfile:
    profile = SettingsProfile(
        settings_hash="test_hash_" + uuid.uuid4().hex[:8],
        name="test_profile",
        values={"ndvi_threshold": 0.5, "min_area_m2": 1000},
    )
    session.add(profile)
    await session.flush()
    return profile


async def _make_analysis_run(session: AsyncSession, user: User) -> AnalysisRun:
    """Создаёт валидный AnalysisRun со всеми зависимостями."""
    query = await _make_query(session, user)
    profile = await _make_settings_profile(session)
    run = AnalysisRun(
        query_id=query.id,
        status="ожидает",
        trace_id="test-trace-" + uuid.uuid4().hex[:8],
        settings_hash=profile.settings_hash,
        params_hash="params_" + uuid.uuid4().hex[:8],
        seed=42,
        rag_index_version="v1",
        model_versions={"llm": "gpt-4", "embedding": "text-embedding-3-small"},
    )
    session.add(run)
    await session.flush()
    return run


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
        c.name for c in cast(Any, AnalysisRun).__table__.constraints if getattr(c, "name", None)
    }
    assert "analysis_run_status_check" in names


def test_analysis_run_attempt_count_check_defined() -> None:
    names = {
        c.name for c in cast(Any, AnalysisRun).__table__.constraints if getattr(c, "name", None)
    }
    assert "analysis_run_attempt_count_check" in names


def test_analysis_run_rejection_category_check_defined() -> None:
    names = {
        c.name for c in cast(Any, AnalysisRun).__table__.constraints if getattr(c, "name", None)
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


# ---------------------------------------------------------------------------
# SettingsProfile — интеграционные тесты (таблица создаётся в схеме)
# ---------------------------------------------------------------------------


async def test_settings_profile_create_valid(db_session: AsyncSession, schema: None) -> None:
    profile = SettingsProfile(
        settings_hash="abc123def456",
        name="default",
        values={"ndvi_threshold": 0.5, "min_area_m2": 1000},
    )
    db_session.add(profile)
    await db_session.flush()
    assert profile.settings_hash == "abc123def456"
    assert profile.name == "default"
    assert profile.values == {"ndvi_threshold": 0.5, "min_area_m2": 1000}
    assert isinstance(profile.created_at, datetime)


async def test_settings_profile_name_required(db_session: AsyncSession, schema: None) -> None:
    profile = SettingsProfile(
        settings_hash="hash-no-name",
        name="",
        values={"key": "value"},
    )
    db_session.add(profile)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_settings_profile_pk_duplicate(db_session: AsyncSession, schema: None) -> None:
    p1 = SettingsProfile(settings_hash="dup-hash", name="a", values={"x": 1})
    p2 = SettingsProfile(settings_hash="dup-hash", name="b", values={"y": 2})
    db_session.add(p1)
    await db_session.flush()
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# RunStage — мета-тесты определений (без вставки, т.к. ссылается на analysis_run.id)
# ---------------------------------------------------------------------------


def test_run_stage_fk_analysis_run() -> None:
    mapper = inspect(RunStage)
    run_col = mapper.columns["run_id"]
    assert any("analysis_run" in str(fk.target_fullname) for fk in run_col.foreign_keys)


def test_run_stage_unique_run_seq() -> None:
    mapper = cast(Any, inspect(RunStage))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_run_stage_run_seq" in index_names


def test_run_stage_index_run_status() -> None:
    mapper = cast(Any, inspect(RunStage))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_run_stage_run_status" in index_names


def test_run_stage_agent_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, RunStage).__table__.constraints
        if getattr(c, "name", None) == "run_stage_agent_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for agent in ("researcher", "analyst", "visualizer", "validator"):
        assert agent in sql


def test_run_stage_status_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, RunStage).__table__.constraints
        if getattr(c, "name", None) == "run_stage_status_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for status in ("running", "completed", "failed", "skipped"):
        assert status in sql


def test_run_stage_description_not_null() -> None:
    mapper = inspect(RunStage)
    column = mapper.columns["description"]
    assert column.nullable is False


def test_run_stage_description_not_empty_check() -> None:
    constraints = [
        c
        for c in cast(Any, RunStage).__table__.constraints
        if getattr(c, "name", None) == "run_stage_description_not_empty"
    ]
    assert len(constraints) == 1


# ---------------------------------------------------------------------------
# ValidationRejection — мета-тесты определений (без вставки, т.к. ссылается на analysis_run.id)
# ---------------------------------------------------------------------------


def test_validation_rejection_fk_analysis_run() -> None:
    mapper = inspect(ValidationRejection)
    run_col = mapper.columns["run_id"]
    assert any("analysis_run" in str(fk.target_fullname) for fk in run_col.foreign_keys)


def test_validation_rejection_unique_run_attempt() -> None:
    mapper = cast(Any, inspect(ValidationRejection))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_validation_rejection_run_attempt" in index_names


def test_validation_rejection_attempt_no_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ValidationRejection).__table__.constraints
        if getattr(c, "name", None) == "validation_rejection_attempt_no_check"
    ]
    assert len(constraints) == 1


def test_validation_rejection_category_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ValidationRejection).__table__.constraints
        if getattr(c, "name", None) == "validation_rejection_category_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for category in ("analysis_error", "data_quality"):
        assert category in sql


def test_validation_rejection_reasons_not_null() -> None:
    mapper = inspect(ValidationRejection)
    column = mapper.columns["reasons"]
    assert column.nullable is False


# ---------------------------------------------------------------------------
# SettingsProfile — мета-тесты определений
# ---------------------------------------------------------------------------


def test_settings_profile_pk_settings_hash() -> None:
    mapper = inspect(SettingsProfile)
    pk_cols = [c.name for c in mapper.primary_key]
    assert pk_cols == ["settings_hash"]


def test_settings_profile_name_not_null() -> None:
    mapper = inspect(SettingsProfile)
    column = mapper.columns["name"]
    assert column.nullable is False


def test_settings_profile_values_not_null() -> None:
    mapper = inspect(SettingsProfile)
    column = mapper.columns["values"]
    assert column.nullable is False


def test_settings_profile_name_not_empty_check() -> None:
    constraints = [
        c
        for c in cast(Any, SettingsProfile).__table__.constraints
        if getattr(c, "name", None) == "settings_profile_name_not_empty"
    ]
    assert len(constraints) == 1


def test_settings_profile_values_not_null_check() -> None:
    constraints = [
        c
        for c in cast(Any, SettingsProfile).__table__.constraints
        if getattr(c, "name", None) == "settings_profile_values_not_null"
    ]
    assert len(constraints) == 1


# ---------------------------------------------------------------------------
# GeocodeCache — интеграционные тесты (раздел 8)
# ---------------------------------------------------------------------------


async def test_geocode_cache_create_valid(db_session: AsyncSession, schema: None) -> None:
    gc = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": '{"display_name":"Москва, Россия"}'},
        geometry=WKTElement("POINT(37.6173 55.7558)", srid=4326),
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc)
    await db_session.flush()
    assert gc.id is not None


async def test_geocode_cache_unique_query_provider(db_session: AsyncSession, schema: None) -> None:
    gc1 = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "a"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    gc2 = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "b"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc1)
    await db_session.flush()
    db_session.add(gc2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# GeocodeCache — мета-тесты определений
# ---------------------------------------------------------------------------


def test_geocode_cache_unique_query_provider_index() -> None:
    mapper = cast(Any, inspect(GeocodeCache))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_geocode_cache_query_provider" in index_names


def test_geocode_cache_geometry_gist_index() -> None:
    mapper = cast(Any, inspect(GeocodeCache))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_geocode_cache_geometry" in index_names


# ---------------------------------------------------------------------------
# ObservationDataset — интеграционные тесты (раздел 10)
# ---------------------------------------------------------------------------


async def test_observation_dataset_create_valid(db_session: AsyncSession, schema: None) -> None:
    obs = ObservationDataset(
        source="earth_search",
        collection="sentinel-2-l2a",
        sensor="MSI",
        scene_id="S2A_MSIL2A_20240101T000000_N0000_R000_T00000",
        acquired_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        cloud_cover=decimal.Decimal("15.5"),
        resolution_m=decimal.Decimal("10.0"),
        extent=WKTElement(
            "POLYGON((37.0 55.0, 38.0 55.0, 38.0 56.0, 37.0 56.0, 37.0 55.0))",
            srid=4326,
        ),
        asset_url="https://example.com/asset.tif",
        checksum="sha256:abc123",
        is_accepted=True,
        rejection_reason=None,
    )
    db_session.add(obs)
    await db_session.flush()
    assert obs.id is not None


async def test_observation_dataset_unique_source_collection_scene(
    db_session: AsyncSession, schema: None
) -> None:
    obs1 = ObservationDataset(
        source="earth_search",
        collection="sentinel-2-l2a",
        sensor="MSI",
        scene_id="S2A_SCENE_001",
        acquired_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        cloud_cover=decimal.Decimal("10.0"),
        resolution_m=decimal.Decimal("10.0"),
        extent=WKTElement("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))", srid=4326),
        asset_url="https://example.com/a.tif",
        is_accepted=True,
    )
    obs2 = ObservationDataset(
        source="earth_search",
        collection="sentinel-2-l2a",
        sensor="MSI",
        scene_id="S2A_SCENE_001",
        acquired_at=datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC),
        cloud_cover=decimal.Decimal("10.0"),
        resolution_m=decimal.Decimal("10.0"),
        extent=WKTElement("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))", srid=4326),
        asset_url="https://example.com/b.tif",
        is_accepted=True,
    )
    db_session.add(obs1)
    await db_session.flush()
    db_session.add(obs2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_observation_dataset_cloud_cover_out_of_range(
    db_session: AsyncSession, schema: None
) -> None:
    obs = ObservationDataset(
        source="earth_search",
        collection="sentinel-2-l2a",
        sensor="MSI",
        scene_id="S2A_CLOUD_BAD",
        acquired_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        cloud_cover=decimal.Decimal("101.0"),
        resolution_m=decimal.Decimal("10.0"),
        extent=WKTElement("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))", srid=4326),
        asset_url="https://example.com/a.tif",
        is_accepted=False,
        rejection_reason="too cloudy",
    )
    db_session.add(obs)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# ObservationDataset — мета-тесты определений
# ---------------------------------------------------------------------------


def test_observation_dataset_unique_source_collection_scene_index() -> None:
    mapper = cast(Any, inspect(ObservationDataset))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_obs_source_collection_scene" in index_names


def test_observation_dataset_extent_gist_index() -> None:
    mapper = cast(Any, inspect(ObservationDataset))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_obs_extent" in index_names


def test_observation_dataset_extent_srid_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ObservationDataset).__table__.constraints
        if getattr(c, "name", None) == "obs_extent_srid_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "ST_SRID(extent) = 4326" in sql


def test_observation_dataset_cloud_cover_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ObservationDataset).__table__.constraints
        if getattr(c, "name", None) == "obs_cloud_cover_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "between 0 and 100" in sql


def test_observation_dataset_source_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ObservationDataset).__table__.constraints
        if getattr(c, "name", None) == "obs_source_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for source in ("earth_search", "planetary_computer", "copernicus"):
        assert source in sql


def test_observation_dataset_sensor_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, ObservationDataset).__table__.constraints
        if getattr(c, "name", None) == "obs_sensor_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for sensor in ("MSI", "OLI/TIRS"):
        assert sensor in sql


# ---------------------------------------------------------------------------
# AreaOfInterest — интеграционные тесты (раздел 9)
# ---------------------------------------------------------------------------


async def test_area_of_interest_create_valid(db_session: AsyncSession, schema: None) -> None:
    gc = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "{}"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc)
    await db_session.flush()

    aoi = AreaOfInterest(
        name="Москва",
        name_source="query_text",
        aoi_type="admin",
        geocode_cache_id=gc.id,
        boundary=WKTElement(
            "POLYGON((37.0 55.0, 38.0 55.0, 38.0 56.0, 37.0 56.0, 37.0 55.0))",
            srid=4326,
        ),
        validity_fixed=False,
        buffer_m=None,
        area_km2=decimal.Decimal("100.5"),
        created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(aoi)
    await db_session.flush()
    assert aoi.id is not None


async def test_area_of_interest_buffer_requires_buffer_m(
    db_session: AsyncSession, schema: None
) -> None:
    gc = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "{}"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc)
    await db_session.flush()

    aoi = AreaOfInterest(
        name="Точка",
        name_source="query_text",
        aoi_type="buffer",
        geocode_cache_id=None,
        boundary=WKTElement("POINT(37.6173 55.7558)", srid=4326),
        validity_fixed=False,
        buffer_m=None,
        area_km2=decimal.Decimal("0.0"),
        created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(aoi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_area_of_interest_invalid_srid_rejected(
    db_session: AsyncSession, schema: None
) -> None:
    gc = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "{}"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc)
    await db_session.flush()

    aoi = AreaOfInterest(
        name="Плохая СРИД",
        name_source="query_text",
        aoi_type="admin",
        geocode_cache_id=gc.id,
        boundary=WKTElement("POINT(37.6173 55.7558)", srid=3857),
        validity_fixed=False,
        buffer_m=None,
        area_km2=decimal.Decimal("0.0"),
        created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(aoi)
    with pytest.raises(SQLAlchemyError):
        await db_session.flush()


async def test_area_of_interest_invalid_geometry_rejected(
    db_session: AsyncSession, schema: None
) -> None:
    gc = GeocodeCache(
        query_normalized="москва",
        provider="nominatim",
        payload={"raw": "{}"},
        geometry=None,
        fetched_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2024, 1, 8, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(gc)
    await db_session.flush()

    aoi = AreaOfInterest(
        name="Невалидная геометрия",
        name_source="query_text",
        aoi_type="admin",
        geocode_cache_id=gc.id,
        boundary=WKTElement("POLYGON((0 0, 1 1, 0 0))", srid=4326),
        validity_fixed=False,
        buffer_m=None,
        area_km2=decimal.Decimal("0.0"),
        created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    db_session.add(aoi)
    with pytest.raises(SQLAlchemyError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# AreaOfInterest — мета-тесты определений
# ---------------------------------------------------------------------------


def test_area_of_interest_boundary_gist_index() -> None:
    mapper = cast(Any, inspect(AreaOfInterest))
    index_names = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_aoi_boundary" in index_names


def test_area_of_interest_boundary_srid_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, AreaOfInterest).__table__.constraints
        if getattr(c, "name", None) == "aoi_boundary_srid_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "ST_SRID(boundary) = 4326" in sql


def test_area_of_interest_boundary_valid_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, AreaOfInterest).__table__.constraints
        if getattr(c, "name", None) == "aoi_boundary_valid_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "ST_IsValid(boundary)" in sql


def test_area_of_interest_buffer_required_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, AreaOfInterest).__table__.constraints
        if getattr(c, "name", None) == "aoi_buffer_required_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "buffer" in sql


def test_area_of_interest_name_source_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, AreaOfInterest).__table__.constraints
        if getattr(c, "name", None) == "aoi_name_source_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "query_text" in sql
    assert "generated" in sql


def test_area_of_interest_type_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, AreaOfInterest).__table__.constraints
        if getattr(c, "name", None) == "aoi_type_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for aoi_type in ("admin", "poi", "buffer"):
        assert aoi_type in sql


def test_area_of_interest_fk_geocode_cache() -> None:
    mapper = inspect(AreaOfInterest)
    col = mapper.columns["geocode_cache_id"]
    assert any("geocode_cache" in str(fk.target_fullname) for fk in col.foreign_keys)


# ---------------------------------------------------------------------------
# RunDataset — мета-тесты определений (без вставки, т.к. ссылается на analysis_run.id)
# ---------------------------------------------------------------------------


def test_run_dataset_pk_composite() -> None:
    mapper = inspect(RunDataset)
    pk_cols = [c.name for c in mapper.primary_key]
    assert set(pk_cols) == {"run_id", "dataset_id", "role"}


def test_run_dataset_fk_analysis_run() -> None:
    mapper = inspect(RunDataset)
    col = mapper.columns["run_id"]
    assert any("analysis_run" in str(fk.target_fullname) for fk in col.foreign_keys)


def test_run_dataset_role_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, RunDataset).__table__.constraints
        if getattr(c, "name", None) == "run_dataset_role_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for role in ("start_period", "end_period"):
        assert role in sql


def test_run_dataset_fk_observation_dataset() -> None:
    mapper = inspect(RunDataset)
    col = mapper.columns["dataset_id"]
    assert any("observation_dataset" in str(fk.target_fullname) for fk in col.foreign_keys)


# ---------------------------------------------------------------------------
# Artifact (раздел 15 data-model.md)
# ---------------------------------------------------------------------------


async def test_artifact_create_valid_preview(db_session: AsyncSession, schema: None) -> None:
    """Artifact без retention_until для preview/final kinds — OK."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="preview_png",
        uri="s3://bucket/preview.png",
        size_bytes=1024,
        checksum="abc123",
        is_heavy=False,
    )
    db_session.add(artifact)
    await db_session.flush()
    assert artifact.id is not None
    assert artifact.retention_until is None


async def test_artifact_create_valid_final_geotiff(db_session: AsyncSession, schema: None) -> None:
    """Artifact без retention_until для final_geotiff — OK."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="final_geotiff",
        uri="s3://bucket/final.tif",
        size_bytes=200 * 1024 * 1024,
        checksum="def456",
        is_heavy=True,
    )
    db_session.add(artifact)
    await db_session.flush()
    assert artifact.id is not None
    assert artifact.retention_until is None


async def test_artifact_create_valid_raw_scene(db_session: AsyncSession, schema: None) -> None:
    """Artifact с retention_until для raw_scene — OK."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    retention = datetime.now(UTC) + timedelta(days=30)
    artifact = Artifact(
        run_id=run.id,
        kind="raw_scene",
        uri="s3://bucket/scene.tif",
        size_bytes=500 * 1024 * 1024,
        checksum="ghi789",
        is_heavy=True,
        retention_until=retention,
    )
    db_session.add(artifact)
    await db_session.flush()
    assert artifact.id is not None
    assert artifact.retention_until == retention


async def test_artifact_create_valid_intermediate_ndvi(
    db_session: AsyncSession, schema: None
) -> None:
    """Artifact с retention_until для intermediate_ndvi — OK."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    retention = datetime.now(UTC) + timedelta(days=7)
    artifact = Artifact(
        run_id=run.id,
        kind="intermediate_ndvi",
        uri="s3://bucket/ndvi.tif",
        size_bytes=150 * 1024 * 1024,
        checksum="jkl012",
        is_heavy=True,
        retention_until=retention,
    )
    db_session.add(artifact)
    await db_session.flush()
    assert artifact.id is not None
    assert artifact.retention_until == retention


async def test_artifact_create_valid_intermediate_classified(
    db_session: AsyncSession, schema: None
) -> None:
    """Artifact с retention_until для intermediate_classified — OK."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    retention = datetime.now(UTC) + timedelta(days=7)
    artifact = Artifact(
        run_id=run.id,
        kind="intermediate_classified",
        uri="s3://bucket/classified.tif",
        size_bytes=180 * 1024 * 1024,
        checksum="mno345",
        is_heavy=True,
        retention_until=retention,
    )
    db_session.add(artifact)
    await db_session.flush()
    assert artifact.id is not None
    assert artifact.retention_until == retention


async def test_artifact_reject_retention_until_for_preview(
    db_session: AsyncSession, schema: None
) -> None:
    """retention_until для preview_png должен вызывать IntegrityError."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="preview_png",
        uri="s3://bucket/preview.png",
        size_bytes=1024,
        checksum="abc123",
        is_heavy=False,
        retention_until=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(artifact)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_artifact_reject_missing_retention_for_raw_scene(
    db_session: AsyncSession, schema: None
) -> None:
    """Отсутствие retention_until для raw_scene должно вызывать IntegrityError."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="raw_scene",
        uri="s3://bucket/scene.tif",
        size_bytes=500 * 1024 * 1024,
        checksum="ghi789",
        is_heavy=True,
        retention_until=None,
    )
    db_session.add(artifact)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_artifact_reject_invalid_kind(db_session: AsyncSession, schema: None) -> None:
    """Недопустимый kind должен вызывать IntegrityError."""
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="invalid_kind",
        uri="s3://bucket/test.tif",
        size_bytes=1024,
        checksum="abc123",
        is_heavy=False,
    )
    db_session.add(artifact)
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_artifact_kind_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, Artifact).__table__.constraints
        if getattr(c, "name", None) == "artifact_kind_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    for kind in (
        "preview_png",
        "final_geotiff",
        "geojson",
        "pdf",
        "raw_scene",
        "intermediate_ndvi",
        "intermediate_classified",
    ):
        assert kind in sql


def test_artifact_retention_until_check_defined() -> None:
    constraints = [
        c
        for c in cast(Any, Artifact).__table__.constraints
        if getattr(c, "name", None) == "artifact_retention_until_check"
    ]
    assert len(constraints) == 1
    sql = str(constraints[0].sqltext)
    assert "retention_until IS NOT NULL" in sql
    assert "raw_scene" in sql
    assert "intermediate_ndvi" in sql
    assert "intermediate_classified" in sql


def test_artifact_fk_analysis_run() -> None:
    mapper = inspect(Artifact)
    col = mapper.columns["run_id"]
    assert any("analysis_run" in str(fk.target_fullname) for fk in col.foreign_keys)


# ---------------------------------------------------------------------------
# ArtifactDeletionLog (раздел 16 data-model.md)
# ---------------------------------------------------------------------------


async def test_artifact_deletion_log_create_valid(db_session: AsyncSession, schema: None) -> None:
    user = await _make_user(db_session)
    run = await _make_analysis_run(db_session, user)
    artifact = Artifact(
        run_id=run.id,
        kind="preview_png",
        uri="s3://bucket/preview.png",
        size_bytes=1024,
        checksum="abc123",
        is_heavy=False,
    )
    db_session.add(artifact)
    await db_session.flush()

    log = ArtifactDeletionLog(
        artifact_id=artifact.id,
        run_id=run.id,
        reason="expired",
    )
    db_session.add(log)
    await db_session.flush()
    assert log.id is not None
    assert isinstance(log.deleted_at, datetime)
    assert log.notified_at is None


def test_artifact_deletion_log_fk_artifact() -> None:
    mapper = inspect(ArtifactDeletionLog)
    col = mapper.columns["artifact_id"]
    assert any("artifact" in str(fk.target_fullname) for fk in col.foreign_keys)


def test_artifact_deletion_log_fk_analysis_run() -> None:
    mapper = inspect(ArtifactDeletionLog)
    col = mapper.columns["run_id"]
    assert any("analysis_run" in str(fk.target_fullname) for fk in col.foreign_keys)


# ---------------------------------------------------------------------------
# KnowledgeDocument (раздел 17 data-model.md)
# ---------------------------------------------------------------------------


async def test_knowledge_document_create_valid(db_session: AsyncSession, schema: None) -> None:
    doc = KnowledgeDocument(
        title="Test Document",
        source="test_source",
        doc_version="1.0",
        checksum="abc123checksum",
        uri="s3://bucket/doc.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    assert doc.id is not None
    assert isinstance(doc.indexed_at, datetime)


async def test_knowledge_document_unique_checksum(db_session: AsyncSession, schema: None) -> None:
    """Уникальность checksum должна соблюдаться."""
    doc1 = KnowledgeDocument(
        title="Doc 1",
        source="test",
        doc_version="1.0",
        checksum="unique_checksum",
        uri="s3://bucket/doc1.pdf",
    )
    doc2 = KnowledgeDocument(
        title="Doc 2",
        source="test",
        doc_version="1.0",
        checksum="unique_checksum",
        uri="s3://bucket/doc2.pdf",
    )
    db_session.add(doc1)
    await db_session.flush()
    db_session.add(doc2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# KnowledgeChunk (раздел 18 data-model.md)
# ---------------------------------------------------------------------------


async def test_knowledge_chunk_create_valid(db_session: AsyncSession, schema: None) -> None:
    """Создание чанка с эмбеддингом (vector(1536))."""
    doc = KnowledgeDocument(
        title="Test Document",
        source="test_source",
        doc_version="1.0",
        checksum="abc123checksum",
        uri="s3://bucket/doc.pdf",
    )
    db_session.add(doc)
    await db_session.flush()

    # Создаём тестовый эмбеддинг (1536 float значений)
    embedding = [0.1] * 1536
    chunk = KnowledgeChunk(
        document_id=doc.id,
        ordinal=0,
        content="Test content " * 50,  # ~800 chars
        embedding=embedding,
        token_count=100,
    )
    db_session.add(chunk)
    await db_session.flush()
    assert chunk.id is not None
    assert chunk.embedding == embedding
    assert chunk.token_count == 100


async def test_knowledge_chunk_unique_document_ordinal(
    db_session: AsyncSession, schema: None
) -> None:
    """UNIQUE(document_id, ordinal) должен соблюдаться."""
    doc = KnowledgeDocument(
        title="Test Document",
        source="test_source",
        doc_version="1.0",
        checksum="abc123checksum_unique",
        uri="s3://bucket/doc.pdf",
    )
    db_session.add(doc)
    await db_session.flush()

    embedding = [0.1] * 1536
    chunk1 = KnowledgeChunk(
        document_id=doc.id,
        ordinal=0,
        content="First chunk",
        embedding=embedding,
        token_count=50,
    )
    chunk2 = KnowledgeChunk(
        document_id=doc.id,
        ordinal=0,  # Тот же ordinal
        content="Second chunk",
        embedding=embedding,
        token_count=50,
    )
    db_session.add(chunk1)
    await db_session.flush()
    db_session.add(chunk2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_knowledge_chunk_unique_document_ordinal_index() -> None:
    """Проверка наличия UNIQUE индекса на (document_id, ordinal)."""
    mapper = inspect(KnowledgeChunk)
    indexes = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_knowledge_chunk_document_ordinal" in indexes


def test_knowledge_chunk_embedding_vector_type() -> None:
    """Проверка что embedding имеет тип Vector(1536)."""
    col = cast(Any, KnowledgeChunk).__table__.columns["embedding"]
    assert col.type.dim == 1536


def test_knowledge_chunk_fk_knowledge_document() -> None:
    mapper = inspect(KnowledgeChunk)
    col = mapper.columns["document_id"]
    assert any("knowledge_document" in str(fk.target_fullname) for fk in col.foreign_keys)


def test_knowledge_chunk_embedding_hnsw_index_defined() -> None:
    """Проверка наличия HNSW индекса на embedding."""
    mapper = inspect(KnowledgeChunk)
    indexes = {ix.name for ix in mapper.mapped_table.indexes}
    assert "ix_knowledge_chunk_embedding" in indexes
