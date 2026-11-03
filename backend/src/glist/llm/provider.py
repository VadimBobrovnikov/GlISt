"""Абстракция LLM-провайдера (задачи T020–T024).

Описывает общий контракт для всех провайдеров (OpenAI, Ollama, Mock) и
базовое исключение, которое выбрасывается при ошибках взаимодействия с LLM.
Все методы асинхронные, чтобы не блокировать event loop графа LangGraph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class LLMProviderError(Exception):
    """Базовое исключение всех ошибок LLM-провайдеров.

    Выбрасывается при сетевых сбоях, таймаутах, rate limit (HTTP 429),
    некорректном ответе от провайдера. Граф может поймать это исключение
    и вернуть пользователю понятное сообщение вместо падения.
    """


class LLMProvider:
    """Абстрактный базовый класс LLM-провайдера.

    Контракт (все методы async):

    - :meth:`generate` — один запрос, возвращает полную строку ответа.
    - :meth:`stream` — потоковая передача ответа кусочками (AsyncIterator[str]).
    - :meth:`embeddings` — эмбеддинги списка текстов, возвращает list[list[float]].

    Аргументы ``**kwargs`` провайдер-зависимые (temperature, max_tokens и т.д.),
    ignorable при отсутствии поддержки провайдером.
    """

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Возвращает полный ответ на промпт.

        Args:
            prompt: Текст промпта.
            **kwargs: Дополнительные параметры провайдера.

        Returns:
            Сгенерированный текст.

        Raises:
            LLMProviderError: при ошибке сети, таймауте или rate limit.
        """
        raise NotImplementedError

    def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Потоковая генерация ответа кусочками.

        Args:
            prompt: Текст промпта.
            **kwargs: Дополнительные параметры провайдера.

        Yields:
            Части ответа по мере готовности.

        Raises:
            LLMProviderError: при ошибке сети, таймауте или rate limit.
        """
        raise NotImplementedError

    async def embeddings(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Возвращает эмбеддинги для списка текстов.

        Args:
            texts: Список строк для эмбеддинга.
            **kwargs: Дополнительные параметры провайдера.

        Returns:
            Список векторов (по одному на каждый входной текст).

        Raises:
            LLMProviderError: при ошибке сети, таймауте или rate limit.
        """
        raise NotImplementedError
