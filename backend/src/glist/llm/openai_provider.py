"""OpenAI-провайдер LLM (задача T021).

Реализует :class:`glist.llm.provider.LLMProvider` через ``httpx`` (без
зависимости от официального OpenAI SDK). Ключ API берётся только из
переменных окружения (через :class:`glist.config.Settings` — Принцип IX).

Обработка ошибок: таймауты и HTTP 429 (rate limit) не роняют граф, а
возвращают :class:`LLMProviderError` с понятным сообщением.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from glist.config import get_settings
from glist.llm.provider import LLMProvider, LLMProviderError

_DEFAULT_TIMEOUT = 60.0


class OpenAIProvider(LLMProvider):
    """Провайдер OpenAI REST API (chat/completions + embeddings).

    Все запросы идут через ``httpx.AsyncClient`` с таймаутом 60 секунд.
    Ключ API читается из ``settings.openai_api_key`` (только из окружения).

    Args:
        api_key: OpenAI API-ключ. Если не передан, берётся из настроек.
        model: Название модели (по умолчанию из ``settings.llm_model``).
        base_url: Базовый URL API (по умолчанию ``https://api.openai.com/v1``).
        timeout: Таймаут запроса в секундах.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        settings = get_settings()
        if api_key is None:
            api_key_setting = settings.openai_api_key
            if api_key_setting is None:
                raise ValueError("OPENAI_API_KEY не задан в окружении")
            self._api_key = api_key_setting.get_secret_value()
        else:
            self._api_key = api_key
        self._model = model or settings.llm_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Полный ответ на один промпт (chat/completions).

        Args:
            prompt: Текст промпта.
            **kwargs: temperature, max_tokens и т.д.

        Returns:
            Сгенерированный текст ответа.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            **kwargs,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"OpenAI generate timeout after {self._timeout}s") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"OpenAI generate request failed: {exc}") from exc

        if response.status_code == 429:
            raise LLMProviderError("OpenAI rate limit exceeded (HTTP 429), попробуйте позже")
        if response.status_code >= 400:
            raise LLMProviderError(
                f"OpenAI generate error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        return str(data["choices"][0]["message"]["content"])

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Потоковая генерация (chat/completions с stream=true).

        Args:
            prompt: Текст промпта.
            **kwargs: temperature, max_tokens и т.д.

        Yields:
            Части текста ответа по мере готовности.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            **kwargs,
        }
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response,
            ):
                if response.status_code == 429:
                    raise LLMProviderError(
                        "OpenAI rate limit exceeded (HTTP 429), попробуйте позже"
                    )
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMProviderError(
                        f"OpenAI stream error {response.status_code}: {body.decode()[:200]}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload_str = line[len("data: ") :]
                    if payload_str.strip() == "[DONE]":
                        break
                    chunk = json.loads(payload_str)
                    delta = chunk["choices"][0]["delta"]
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"OpenAI stream timeout after {self._timeout}s") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"OpenAI stream request failed: {exc}") from exc

    async def embeddings(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Эмбеддинги списка текстов (embeddings endpoint).

        Args:
            texts: Список строк.
            **kwargs: Дополнительные параметры.

        Returns:
            Список векторов длиной selon модели.

        Raises:
            LLMProviderError: при таймауте, 429 или иной HTTP-ошибке.
        """
        payload: dict[str, Any] = {
            "model": get_settings().embedding_model,
            "input": texts,
            **kwargs,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"OpenAI embeddings timeout after {self._timeout}s") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"OpenAI embeddings request failed: {exc}") from exc

        if response.status_code == 429:
            raise LLMProviderError("OpenAI rate limit exceeded (HTTP 429), попробуйте позже")
        if response.status_code >= 400:
            raise LLMProviderError(
                f"OpenAI embeddings error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        return [item["embedding"] for item in data["data"]]
