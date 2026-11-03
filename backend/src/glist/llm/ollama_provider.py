"""Ollama-провайдер LLM (задача T022).

Реализует :class:`glist.llm.provider.LLMProvider` для локального Ollama
через ``httpx``. API-ключи не требуются (локальный запуск).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from glist.config import get_settings
from glist.llm.provider import LLMProvider, LLMProviderError

_DEFAULT_TIMEOUT = 120.0  # Ollama может быть медленнее OpenAI


class OllamaProvider(LLMProvider):
    """Провайдер локального Ollama (API /api/generate, /api/embeddings).

    Ollama запускается локально, API-ключи не нужны. Базовый URL берется
    из ``settings.ollama_base_url`` (по умолчанию http://localhost:11434).

    Args:
        model: Название модели Ollama (по умолчанию из ``settings.llm_model``).
        base_url: Базовый URL Ollama API.
        timeout: Таймаут запроса в секундах.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str = "http://localhost:11434",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.llm_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Полный ответ на один промпт (Ollama /api/generate).

        Args:
            prompt: Текст промпта.
            **kwargs: temperature, num_predict и т.д.

        Returns:
            Сгенерированный текст ответа.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            **kwargs,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"Ollama generate timeout after {self._timeout}s") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"Ollama generate request failed: {exc}") from exc

        if response.status_code == 429:
            raise LLMProviderError("Ollama rate limit exceeded (HTTP 429), попробуйте позже")
        if response.status_code >= 400:
            raise LLMProviderError(
                f"Ollama generate error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        return str(data.get("response", ""))

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Потоковая генерация (Ollama /api/generate с stream=true).

        Args:
            prompt: Текст промпта.
            **kwargs: temperature, num_predict и т.д.

        Yields:
            Части текста ответа по мере готовности.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": True,
            **kwargs,
        }
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/api/generate",
                    json=payload,
                ) as response,
            ):
                if response.status_code == 429:
                    raise LLMProviderError(
                        "Ollama rate limit exceeded (HTTP 429), попробуйте позже"
                    )
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMProviderError(
                        f"Ollama stream error {response.status_code}: {body.decode()[:200]}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done", False):
                        break
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"Ollama stream timeout after {self._timeout}s") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"Ollama stream request failed: {exc}") from exc

    async def embeddings(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Эмбеддинги списка текстов (Ollama /api/embeddings).

        Args:
            texts: Список строк.
            **kwargs: Дополнительные параметры.

        Returns:
            Список векторов длиной selon модели.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        results: list[list[float]] = []
        for text in texts:
            payload: dict[str, Any] = {
                "model": get_settings().embedding_model,
                "prompt": text,
                **kwargs,
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/api/embeddings",
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                raise LLMProviderError(f"Ollama embeddings timeout after {self._timeout}s") from exc
            except httpx.RequestError as exc:
                raise LLMProviderError(f"Ollama embeddings request failed: {exc}") from exc

            if response.status_code == 429:
                raise LLMProviderError("Ollama rate limit exceeded (HTTP 429), попробуйте позже")
            if response.status_code >= 400:
                raise LLMProviderError(
                    f"Ollama embeddings error {response.status_code}: {response.text[:200]}"
                )

            data = response.json()
            results.append(data.get("embedding", []))

        return results
