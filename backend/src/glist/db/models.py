"""SQLModel-модели данных (задача T016).

Разделы 1–4, 7–14 спецификации data-model.md:
user, query, clarification_request, analysis_run, run_stage,
validation_rejection, settings_profile, geocode_cache,
area_of_interest, observation_dataset, run_dataset,
analysis_result, result_territory, change_zone.
"""

from __future__ import annotations

import decimal
import uuid
from datetime import UTC, date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, SmallInteger, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

_COMMON_STATUSES = (
    "ожидает",
    "в обработке",
    "завершён",
    "не прошёл проверку",
    "данные недоступны",
    "ошибка",
)

_STATUSES_SQL = ", ".join(f"'{s}'" for s in _COMMON_STATUSES)


class User(SQLModel, table=True):
    """Пользователь системы (раздел 1 data-model.md)."""

    __tablename__ = "user"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    display_name: str = Field(default="Пользователь", nullable=False)
    is_local: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("length(display_name) > 0", name="user_display_name_nonempty"),
    )


class Query(SQLModel, table=True):
    """Пользовательский запрос (раздел 2 data-model.md)."""

    __tablename__ = "query"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    text: str = Field(nullable=False)
    language: str = Field(nullable=False)
    query_kind: str = Field(nullable=False)
    parent_query_id: uuid.UUID | None = Field(default=None, foreign_key="query.id")
    extracted: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    status: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES_SQL})", name="query_status_check"),
        CheckConstraint("language IN ('ru', 'en')", name="query_language_check"),
        CheckConstraint("query_kind IN ('primary', 'follow_up')", name="query_kind_check"),
        CheckConstraint("length(text) between 3 and 1000", name="query_text_length_check"),
        Index("ix_query_user_created_at", "user_id", "created_at"),
        Index("ix_query_parent_query_id", "parent_query_id"),
    )


class ClarificationRequest(SQLModel, table=True):
    """Запрос уточнения (раздел 3 data-model.md)."""

    __tablename__ = "clarification_request"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    query_id: uuid.UUID = Field(foreign_key="query.id", nullable=False)
    kind: str = Field(nullable=False)
    question_text: str = Field(nullable=False)
    options: list[dict[str, Any]] | None = Field(default=None, sa_type=JSONB)
    selected_option: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    status: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = Field(default=None)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('ambiguity', 'no_data_alternatives')",
            name="clarification_request_kind_check",
        ),
        CheckConstraint(
            "status IN ('ожидает ответа', 'отвечен', 'истёк')",
            name="clarification_request_status_check",
        ),
    )


class AnalysisRun(SQLModel, table=True):
    """Запуск анализа (раздел 4 data-model.md)."""

    __tablename__ = "analysis_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    query_id: uuid.UUID = Field(foreign_key="query.id", nullable=False)
    parent_run_id: uuid.UUID | None = Field(default=None, foreign_key="analysis_run.id")
    reused_result_id: uuid.UUID | None = Field(
        default=None,
        sa_column_args=[
            ForeignKey(
                "analysis_result.id",
                name="fk_analysis_run_reused_result",
                use_alter=True,
            )
        ],
    )
    status: str = Field(nullable=False)
    status_reason: str | None = Field(default=None)
    queue_position: int | None = Field(default=None)
    queued_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    attempt_count: int = Field(default=0, nullable=False)
    rejection_category: str | None = Field(default=None)
    trace_id: str = Field(nullable=False)
    settings_hash: str = Field(foreign_key="settings_profile.settings_hash", nullable=False)
    params_hash: str = Field(nullable=False)
    seed: int = Field(nullable=False)
    rag_index_version: str = Field(nullable=False)
    model_versions: dict[str, Any] = Field(sa_type=JSONB, nullable=False)
    is_reference: bool = Field(default=False, nullable=False)
    is_pinned: bool = Field(default=False, nullable=False)
    retention_until: datetime | None = Field(default=None)
    purge_notice_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES_SQL})", name="analysis_run_status_check"),
        CheckConstraint("attempt_count between 0 and 1", name="analysis_run_attempt_count_check"),
        CheckConstraint(
            "rejection_category IS NULL OR rejection_category "
            "IN ('analysis_error', 'data_quality')",
            name="analysis_run_rejection_category_check",
        ),
        Index("ix_analysis_run_status_queue_position", "status", "queue_position"),
        Index("ix_analysis_run_query_created_at", "query_id", "created_at"),
        Index("ix_analysis_run_params_hash", "params_hash"),
        Index(
            "ix_analysis_run_is_reference",
            "is_reference",
            postgresql_where=text("is_reference"),
        ),
        Index(
            "ix_analysis_run_retention_until",
            "retention_until",
            postgresql_where=text("retention_until is not null"),
        ),
    )


class RunStage(SQLModel, table=True):
    """Этап запуска анализа (раздел 5 data-model.md)."""

    __tablename__ = "run_stage"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="analysis_run.id", nullable=False, ondelete="CASCADE")
    seq: int = Field(sa_type=SmallInteger, nullable=False)
    attempt_no: int = Field(sa_type=SmallInteger, default=0, nullable=False)
    agent: str = Field(nullable=False)
    stage_code: str = Field(nullable=False)
    title: str = Field(nullable=False)
    description: str = Field(nullable=False)
    status: str = Field(nullable=False)
    details: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)

    __table_args__ = (
        CheckConstraint(
            "agent IN ('researcher', 'analyst', 'visualizer', 'validator')",
            name="run_stage_agent_check",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name="run_stage_status_check",
        ),
        CheckConstraint("length(description) > 0", name="run_stage_description_not_empty"),
        Index("ix_run_stage_run_seq", "run_id", "seq", unique=True),
        Index("ix_run_stage_run_status", "run_id", "status"),
    )


class ValidationRejection(SQLModel, table=True):
    """Отклонение валидатором (раздел 6 data-model.md)."""

    __tablename__ = "validation_rejection"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="analysis_run.id", nullable=False, ondelete="CASCADE")
    attempt_no: int = Field(sa_type=SmallInteger, nullable=False)
    category: str = Field(nullable=False)
    reasons: list[dict[str, Any]] | None = Field(default=None, sa_type=JSONB, nullable=False)
    fixes_applied: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("attempt_no between 1 and 2", name="validation_rejection_attempt_no_check"),
        CheckConstraint(
            "category IN ('analysis_error', 'data_quality')",
            name="validation_rejection_category_check",
        ),
        Index(
            "ix_validation_rejection_run_attempt",
            "run_id",
            "attempt_no",
            unique=True,
        ),
    )


class SettingsProfile(SQLModel, table=True):
    """Профиль настроек анализа (раздел 7 data-model.md)."""

    __tablename__ = "settings_profile"

    settings_hash: str = Field(primary_key=True)
    name: str = Field(nullable=False)
    values: dict[str, Any] = Field(sa_type=JSONB, nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("length(name) > 0", name="settings_profile_name_not_empty"),
        CheckConstraint("values IS NOT NULL", name="settings_profile_values_not_null"),
    )


class GeocodeCache(SQLModel, table=True):
    """Кэш геокодирования (раздел 8 data-model.md)."""

    __tablename__ = "geocode_cache"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    query_normalized: str = Field(nullable=False)
    provider: str = Field(nullable=False)
    payload: dict[str, Any] = Field(sa_type=JSONB, nullable=False)
    geometry: Any = Field(sa_type=Geometry(geometry_type="Geometry", srid=4326), nullable=True)
    fetched_at: datetime = Field(nullable=False)
    expires_at: datetime = Field(nullable=False)

    __table_args__ = (
        Index(
            "ix_geocode_cache_query_provider",
            "query_normalized",
            "provider",
            unique=True,
        ),
        Index("ix_geocode_cache_geometry", "geometry", postgresql_using="gist"),
    )


class AreaOfInterest(SQLModel, table=True):
    """Область интереса (раздел 9 data-model.md)."""

    __tablename__ = "area_of_interest"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(nullable=False)
    name_source: str = Field(nullable=False)
    aoi_type: str = Field(nullable=False)
    geocode_cache_id: uuid.UUID | None = Field(default=None, foreign_key="geocode_cache.id")
    boundary: Any = Field(sa_type=Geometry(geometry_type="Geometry", srid=4326), nullable=False)
    validity_fixed: bool = Field(default=False, nullable=False)
    buffer_m: int | None = Field(default=None)
    area_km2: decimal.Decimal = Field(sa_type=Numeric(12, 4), nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("ST_SRID(boundary) = 4326", name="aoi_boundary_srid_check"),
        CheckConstraint("ST_IsValid(boundary)", name="aoi_boundary_valid_check"),
        CheckConstraint(
            "aoi_type != 'buffer' OR buffer_m IS NOT NULL",
            name="aoi_buffer_required_check",
        ),
        CheckConstraint(
            "name_source IN ('query_text', 'generated')",
            name="aoi_name_source_check",
        ),
        CheckConstraint("aoi_type IN ('admin', 'poi', 'buffer')", name="aoi_type_check"),
        Index("ix_aoi_boundary", "boundary", postgresql_using="gist"),
        Index("ix_aoi_name", "name"),
    )


class ObservationDataset(SQLModel, table=True):
    """Наблюдаемый набор данных (раздел 10 data-model.md)."""

    __tablename__ = "observation_dataset"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str = Field(nullable=False)
    collection: str = Field(nullable=False)
    sensor: str = Field(nullable=False)
    scene_id: str = Field(nullable=False)
    acquired_at: datetime = Field(nullable=False)
    cloud_cover: decimal.Decimal = Field(sa_type=Numeric(5, 2), nullable=False)
    resolution_m: decimal.Decimal = Field(sa_type=Numeric(6, 2), nullable=False)
    extent: Any = Field(sa_type=Geometry(geometry_type="Polygon", srid=4326), nullable=False)
    asset_url: str = Field(nullable=False)
    checksum: str | None = Field(default=None)
    is_accepted: bool = Field(nullable=False)
    rejection_reason: str | None = Field(default=None)

    __table_args__ = (
        CheckConstraint("cloud_cover between 0 and 100", name="obs_cloud_cover_check"),
        CheckConstraint("ST_SRID(extent) = 4326", name="obs_extent_srid_check"),
        CheckConstraint(
            "source IN ('earth_search', 'planetary_computer', 'copernicus')",
            name="obs_source_check",
        ),
        CheckConstraint(
            "collection IN ('sentinel-2-l2a', 'landsat-c2-l2')",
            name="obs_collection_check",
        ),
        CheckConstraint("sensor IN ('MSI', 'OLI/TIRS')", name="obs_sensor_check"),
        Index("ix_obs_collection_acquired", "collection", "acquired_at"),
        Index(
            "ix_obs_source_collection_scene",
            "source",
            "collection",
            "scene_id",
            unique=True,
        ),
        Index("ix_obs_extent", "extent", postgresql_using="gist"),
    )


class RunDataset(SQLModel, table=True):
    """Связь запуска с набором данных (раздел 11 data-model.md)."""

    __tablename__ = "run_dataset"

    run_id: uuid.UUID = Field(
        foreign_key="analysis_run.id",
        nullable=False,
        ondelete="CASCADE",
        primary_key=True,
    )
    dataset_id: uuid.UUID = Field(
        foreign_key="observation_dataset.id",
        nullable=False,
        primary_key=True,
    )
    role: str = Field(nullable=False, primary_key=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('start_period', 'end_period')",
            name="run_dataset_role_check",
        ),
    )


class AnalysisResult(SQLModel, table=True):
    """Результат анализа (раздел 12 data-model.md)."""

    __tablename__ = "analysis_result"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(
        foreign_key="analysis_run.id",
        nullable=False,
        ondelete="CASCADE",
        unique=True,
    )
    params_hash: str = Field(nullable=False, unique=True)
    mode: str = Field(nullable=False)
    territories_count: int = Field(sa_type=SmallInteger, nullable=False)
    has_overlap: bool = Field(nullable=False)
    total_area_km2: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    summary_text: str = Field(nullable=False)
    explanation_text: str = Field(nullable=False)
    citations: dict[str, Any] = Field(sa_type=JSONB, nullable=False)
    sources_summary: dict[str, Any] = Field(sa_type=JSONB, nullable=False)
    crs: str = Field(default="EPSG:4326", nullable=False)
    validation_outcome: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint(
            "mode IN ('single', 'combined')",
            name="analysis_result_mode_check",
        ),
        CheckConstraint(
            "territories_count between 1 and 5",
            name="analysis_result_territories_count_check",
        ),
        CheckConstraint(
            "validation_outcome IN ('passed', 'failed')",
            name="analysis_result_validation_outcome_check",
        ),
        Index("ix_analysis_result_created_at", "created_at"),
    )


class ResultTerritory(SQLModel, table=True):
    """Территория результата (раздел 13 data-model.md)."""

    __tablename__ = "result_territory"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    result_id: uuid.UUID = Field(
        foreign_key="analysis_result.id",
        nullable=False,
        ondelete="CASCADE",
    )
    aoi_id: uuid.UUID = Field(
        foreign_key="area_of_interest.id",
        nullable=False,
    )
    position: int = Field(sa_type=SmallInteger, nullable=False)
    territory_name: str = Field(nullable=False)
    retained_area_km2: decimal.Decimal = Field(
        sa_type=Numeric(12, 4), default=decimal.Decimal("0"), nullable=False
    )
    lost_area_km2: decimal.Decimal = Field(
        sa_type=Numeric(12, 4), default=decimal.Decimal("0"), nullable=False
    )
    new_area_km2: decimal.Decimal = Field(
        sa_type=Numeric(12, 4), default=decimal.Decimal("0"), nullable=False
    )
    source_run_id: uuid.UUID = Field(
        foreign_key="analysis_run.id",
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "position between 1 and 5",
            name="result_territory_position_check",
        ),
        Index(
            "ix_result_territory_result_position",
            "result_id",
            "position",
            unique=True,
        ),
        Index("ix_result_territory_aoi_id", "aoi_id"),
    )


class ChangeZone(SQLModel, table=True):
    """Зона изменений (раздел 14 data-model.md)."""

    __tablename__ = "change_zone"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    result_territory_id: uuid.UUID = Field(
        foreign_key="result_territory.id",
        nullable=False,
        ondelete="CASCADE",
    )
    change_class: str = Field(nullable=False)
    geom: Any = Field(sa_type=Geometry(geometry_type="MultiPolygon", srid=4326), nullable=False)
    area_m2: decimal.Decimal = Field(sa_type=Numeric(18, 2), nullable=False)
    area_km2: decimal.Decimal = Field(sa_type=Numeric(12, 4), nullable=False)
    period_start: date = Field(nullable=False)
    period_end: date = Field(nullable=False)
    cleaned: bool = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("ST_SRID(geom) = 4326", name="change_zone_geom_srid_check"),
        CheckConstraint("ST_IsValid(geom)", name="change_zone_geom_valid_check"),
        CheckConstraint(
            "change_class IN ('retained', 'lost', 'new')",
            name="change_zone_change_class_check",
        ),
        Index("ix_change_zone_geom", "geom", postgresql_using="gist"),
        Index(
            "ix_change_zone_territory_class",
            "result_territory_id",
            "change_class",
        ),
    )
