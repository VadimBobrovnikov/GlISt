"""Конфигурация приложения GlISt (задача T011).

Единственный источник настроек — переменные окружения (и локальный ``.env`` как их
файловое представление): код не хранит секретов и не задаёт для них значений по
умолчанию (Принцип IX). Секреты типизированы ``SecretStr`` и маскируются в repr/логах.

Состав полей один в один повторяет ``.env.example`` (задача T005): поле
``llm_provider`` читается из переменной ``LLM_PROVIDER`` (pydantic-settings
сопоставляет имена без учёта регистра и без переименований).

Доступ: ``get_settings()`` — ленивый кэшированный вызов. Модуль не создаёт настройки
на этапе импорта, поэтому импорт безопасен при незаполненном окружении; очистка
кэша в тестах — ``get_settings.cache_clear()``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Пустое значение в .env (например, ``LANGFUSE_INIT_ORG_ID=``) означает «не задано».
_EMPTY_IS_NONE_FIELDS = (
    "postgres_password",
    "redis_auth",
    "langfuse_init_secret_key",
    "langfuse_init_user_password",
    "langfuse_init_org_id",
    "langfuse_init_project_id",
    "langfuse_db_password",
    "langfuse_salt",
    "langfuse_encryption_key",
    "clickhouse_password",
    "minio_root_password",
    "openai_api_key",
)


class Settings(BaseSettings):
    """Настройки стенда и приложения — все из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # docker-compose и CI передают в контейнер служебные переменные сверх списка.
        extra="ignore",
    )

    # -------------------------------------------------- PostgreSQL (alembic и инструменты с хоста)
    # Внутри контейнеров api/worker docker-compose.yml перекрывает DSN адресом сервиса `postgres`.
    database_url: SecretStr
    postgres_user: str = "glist"
    postgres_password: SecretStr | None = None
    postgres_db: str = "glist"

    # ------------------------------------------------------- Redis (ARQ — БД 0, LangFuse — БД 1)
    redis_url: SecretStr
    redis_auth: SecretStr | None = None

    # ------------------------------------------------------- LangFuse (трассировка — Принцип III)
    langfuse_host: str
    langfuse_public_url: str | None = None
    langfuse_public_key: str
    langfuse_secret_key: SecretStr
    # INIT-ключи совпадают с SDK-ключами: при отсутствии берутся они же (_apply_defaults).
    langfuse_init_public_key: str | None = None
    langfuse_init_secret_key: SecretStr | None = None
    langfuse_init_org_id: str | None = None
    langfuse_init_org_name: str | None = None
    langfuse_init_project_id: str | None = None
    langfuse_init_project_name: str | None = None
    langfuse_init_user_email: str | None = None
    langfuse_init_user_name: str | None = None
    langfuse_init_user_password: SecretStr | None = None
    # Внутренности LangFuse v4 (см. docker-compose.yml).
    langfuse_db_user: str = "langfuse"
    langfuse_db_password: SecretStr | None = None
    langfuse_salt: SecretStr | None = None
    langfuse_encryption_key: SecretStr | None = None
    clickhouse_user: str = "clickhouse"
    clickhouse_password: SecretStr | None = None

    # -------------------------------------------------- MinIO (EOL: plan.md, отклонение №5)
    minio_root_user: str = "minio"
    minio_root_password: SecretStr | None = None

    # -------------------------------------------------- LLM-провайдер (абстракция LLMProvider, VI)
    llm_provider: Literal["openai", "ollama"] = "openai"
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: SecretStr | None = None

    # ------------------------------------------------------- Воспроизводимость и геокодирование
    seed: int = 42
    osm_user_agent: str
    artifacts_dir: Path = Path("./data/artifacts")

    # Тестовый режим: переменной нет в .env.example, задаётся только тестовым окружением;
    # T025 обязан уважать её — в тестах реальная трассировка отключается.
    glist_testing: bool = False

    @field_validator(*_EMPTY_IS_NONE_FIELDS, mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """Пустая строка окружения равнозначна отсутствию значения."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _apply_defaults(self) -> Settings:
        """Значения по умолчанию, вычисляемые из других полей, и проверка LLM-провайдера."""
        if not self.langfuse_public_url:
            self.langfuse_public_url = self.langfuse_host
        if not self.langfuse_init_public_key:
            self.langfuse_init_public_key = self.langfuse_public_key
        if self.langfuse_init_secret_key is None:
            self.langfuse_init_secret_key = self.langfuse_secret_key
        if self.llm_provider == "openai" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OPENAI_API_KEY обязателен при LLM_PROVIDER=openai")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает настройки, прочитанные из окружения один раз за процесс."""
    return Settings()
