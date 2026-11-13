"""Реестр и фабрика LLM-провайдеров (задача T024).

Функция :func:`get_llm_provider` читает ``settings.llm_provider`` и возвращает
экземпляр соответствующего провайдера: ``OpenAIProvider`` или ``OllamaProvider``.
"""

from __future__ import annotations

import os
from typing import Any

from glist.config import get_settings
from glist.llm.ollama_provider import OllamaProvider
from glist.llm.openai_provider import OpenAIProvider
from glist.llm.provider import LLMProvider

_TESTING_VALUES = frozenset({"1", "true", "yes", "on"})


def _is_testing() -> bool:
    """Тестовый режим включается переменной ``GLIST_TESTING`` (offline CI)."""
    return os.environ.get("GLIST_TESTING", "").strip().lower() in _TESTING_VALUES


def _seed_from_env() -> int:
    """Seed мок-провайдера из окружения (по умолчанию 42)."""
    try:
        return int(os.environ.get("SEED", "42"))
    except ValueError:
        return 42


def get_llm_provider(**kwargs: Any) -> LLMProvider:
    """Возвращает LLM-провайдер согласно ``settings.llm_provider``.

    Читает настройки из окружения (через ``get_settings()``). Поддерживаемые
    значения ``llm_provider``: ``"openai"``, ``"ollama"``.

    Args:
        **kwargs: Передаются в конструктор выбранного провайдера
            (api_key, model, base_url, timeout и т.д.).

    Returns:
        Экземпляр ``OpenAIProvider`` или ``OllamaProvider``.

    Raises:
        ValueError: если ``llm_provider`` имеет неизвестное значение.
    """
    if _is_testing():
        # В тестах реальные провайдеры не используются (Принцип VI, offline CI).
        # Проверка идёт до get_settings(): тестам не нужны полные env стенда.
        from glist.llm.mock_provider import MockLLMProvider

        return MockLLMProvider(seed=_seed_from_env())

    settings = get_settings()
    provider_name = settings.llm_provider

    if provider_name == "openai":
        kwargs.setdefault("model", settings.llm_model)
        return OpenAIProvider(**kwargs)
    if provider_name == "ollama":
        kwargs.setdefault("model", settings.llm_model)
        # Адрес Ollama строго из OLLAMA_BASE_URL (в контейнере — host.docker.internal).
        kwargs.setdefault("base_url", settings.ollama_base_url)
        return OllamaProvider(**kwargs)

    raise ValueError(
        f"Неизвестный LLM_PROVIDER={provider_name!r}. Ожидается 'openai' или 'ollama'."
    )
