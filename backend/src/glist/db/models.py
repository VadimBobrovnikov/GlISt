"""SQLModel-модели данных (задача T013).

Разделы 1–4 спецификации data-model.md:
user, query, clarification_request, analysis_run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, text
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
    parent_query_id: uuid.UUID | None = Field(
        default=None, foreign_key="query.id"
    )
    extracted: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    status: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_STATUSES_SQL})", name="query_status_check"
        ),
        CheckConstraint("language IN ('ru', 'en')", name="query_language_check"),
        CheckConstraint(
            "query_kind IN ('primary', 'follow_up')", name="query_kind_check"
        ),
        CheckConstraint(
            "length(text) between 3 and 1000", name="query_text_length_check"
        ),
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
    parent_run_id: uuid.UUID | None = Field(
        default=None, foreign_key="analysis_run.id"
    )
    reused_result_id: uuid.UUID | None = Field(
        default=None, foreign_key="analysis_result.id"
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
    settings_hash: str = Field(
        foreign_key="settings_profile.settings_hash", nullable=False
    )
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
        CheckConstraint(
            f"status IN ({_STATUSES_SQL})", name="analysis_run_status_check"
        ),
        CheckConstraint(
            "attempt_count between 0 and 1", name="analysis_run_attempt_count_check"
        ),
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
