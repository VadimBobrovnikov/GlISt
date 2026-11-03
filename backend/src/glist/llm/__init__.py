"""Реестр и фабрика LLM-провайдеров (задача T024).

Функция :func:`get_llm_provider` читает ``settings.llm_provider`` и возвращает
экземпляр соответствующего провайдера: ``OpenAIProvider`` или ``OllamaProvider``.
"""

from __future__ import annotations

from typing import Any

from glist.config import get_settings
from glist.llm.ollama_provider import OllamaProvider
from glist.llm.openai_provider import OpenAIProvider
from glist.llm.provider import LLMProvider


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
    settings = get_settings()
    provider_name = settings.llm_provider

    if provider_name == "openai":
        return OpenAIProvider(**kwargs)
    if provider_name == "ollama":
        return OllamaProvider(**kwargs)

    raise ValueError(
        f"Неизвестный LLM_PROVIDER={provider_name!r}. Ожидается 'openai' или 'ollama'."
    )
