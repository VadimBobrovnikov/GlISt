"""Тесты конфигурации приложения (задача T011).

Проверяют соответствие ``Settings`` переменным из ``.env.example``, маскирование
секретов (Принцип IX), вычисляемые значения LangFuse и валидацию LLM-провайдера.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from glist.config import Settings, get_settings


@pytest.fixture
def settings_env(required_env: dict[str, str]) -> dict[str, str]:
    """Обязательные переменные окружения (общая фикстура ``required_env`` в conftest)."""
    return required_env


def _settings(**overrides: Any) -> Settings:
    """Строит настройки только из окружения (без .env, чтобы тесты были детерминированы)."""
    return Settings(_env_file=None, **overrides)


def test_reads_variables_from_environment(
    settings_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("SEED", "7")
    settings = _settings()
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.llm_model == "gpt-4o"
    assert settings.seed == 7
    assert settings.osm_user_agent.startswith("GlISt-Research")


def test_defaults_match_env_example(
    settings_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # autouse-фикстура T008 выставляет GLIST_TESTING=1 — здесь проверяем значение по умолчанию.
    monkeypatch.delenv("GLIST_TESTING", raising=False)
    settings = _settings()
    assert settings.postgres_user == "glist"
    assert settings.postgres_db == "glist"
    assert settings.langfuse_db_user == "langfuse"
    assert settings.clickhouse_user == "clickhouse"
    assert settings.minio_root_user == "minio"
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.seed == 42
    assert settings.artifacts_dir.parts[-2:] == ("data", "artifacts")
    assert settings.glist_testing is False


def test_langfuse_values_fall_back_to_sdk_keys(settings_env: dict[str, str]) -> None:
    settings = _settings()
    assert settings.langfuse_public_url == "http://localhost:3000"
    assert settings.langfuse_init_public_key == "pk-test"
    assert settings.langfuse_init_secret_key is not None
    assert settings.langfuse_init_secret_key.get_secret_value() == "sk-test"


def test_explicit_langfuse_init_values_are_kept(settings_env: dict[str, str]) -> None:
    settings = _settings(
        LANGFUSE_PUBLIC_URL="https://langfuse.example.org",
        LANGFUSE_INIT_PUBLIC_KEY="pk-init",
        LANGFUSE_INIT_SECRET_KEY="sk-init",
    )
    assert settings.langfuse_public_url == "https://langfuse.example.org"
    assert settings.langfuse_init_public_key == "pk-init"
    assert settings.langfuse_init_secret_key is not None
    assert settings.langfuse_init_secret_key.get_secret_value() == "sk-init"


def test_empty_optional_values_become_none(settings_env: dict[str, str]) -> None:
    settings = _settings(
        LLM_PROVIDER="ollama",
        OPENAI_API_KEY="",
        POSTGRES_PASSWORD="",
        LANGFUSE_INIT_ORG_ID="   ",
        LANGFUSE_INIT_PROJECT_ID="",
    )
    assert settings.openai_api_key is None
    assert settings.postgres_password is None
    assert settings.langfuse_init_org_id is None
    assert settings.langfuse_init_project_id is None


def test_openai_provider_requires_api_key(settings_env: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        _settings(OPENAI_API_KEY="")


def test_ollama_provider_does_not_require_api_key(settings_env: dict[str, str]) -> None:
    settings = _settings(LLM_PROVIDER="ollama", OPENAI_API_KEY="")
    assert settings.llm_provider == "ollama"
    assert settings.openai_api_key is None


def test_unknown_provider_is_rejected(settings_env: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        _settings(LLM_PROVIDER="local-model")


def test_secrets_are_masked(settings_env: dict[str, str]) -> None:
    settings = _settings(POSTGRES_PASSWORD="db_secret", REDIS_AUTH="redis_secret")
    rendered = repr(settings)
    assert "db_secret" not in rendered
    assert "redis_secret" not in rendered
    assert "sk-test" not in rendered
    # Значения доступны только явным вызовом — для подключения к сервисам.
    assert settings.langfuse_secret_key.get_secret_value() == "sk-test"


def test_testing_flag_from_environment(settings_env: dict[str, str]) -> None:
    assert _settings(GLIST_TESTING="1").glist_testing is True


def test_unknown_environment_variables_are_ignored(settings_env: dict[str, str]) -> None:
    assert _settings(SOME_SERVICE_INTERNAL_VAR="value").llm_provider == "openai"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _BASE_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
