"""MockLLMProvider — детерминированный мок для тестов (задача T023).

Ответы детерминированы по (seed, prompt). Поддерживаются сценарии валидатора:
success, analysis_error, data_quality. Выбор сценария — через атрибут
``scenario`` или директиву ``[[scenario:…]]`` в промпте.

Все вызовы записываются в ``calls`` для assert в тестах.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

from glist.llm.provider import LLMProvider

Scenario = Literal["success", "analysis_error", "data_quality"]

_SCENARIO_RESPONSES: dict[Scenario, str] = {
    "analysis_error": (
        "MOCK_REJECT[analysis_error]: расчётная площадь противоречит исходным данным"
    ),
    "data_quality": ("MOCK_REJECT[data_quality]: данные не покрывают запрошенный период анализа"),
}

_EMBEDDING_DIM = 1536

_SCENARIO_DIRECTIVE = re.compile(r"\[\[scenario:(success|analysis_error|data_quality)\]\]")


class MockLLMProvider(LLMProvider):
    """Мок LLM-провайдера с детерминированными ответами по сиду.

    Детерминированность: ``generate``/``stream`` — чистая функция ``(seed, prompt)``,
    ``embeddings`` — единичный вектор длины 1536 из генератора с сидом ``seed + текст``
    (Принцип II). Сценарий валидатора выбирается атрибутом ``scenario`` или директивой
    ``[[scenario:analysis_error]]`` в промпте (директива из промпта удаляется).

    Args:
        seed: Сид для детерминированной генерации (по умолчанию 42).
        scenario: Сценарий ответа: "success", "analysis_error", "data_quality".
    """

    def __init__(self, seed: int = 42, scenario: Scenario = "success") -> None:
        self.seed = seed
        self.scenario = scenario
        self.calls: list[dict[str, Any]] = []

    def _resolve(self, prompt: str) -> tuple[Scenario, str]:
        """Возвращает (сценарий, очищенный_промпт) с учётом директивы в тексте."""
        match = _SCENARIO_DIRECTIVE.search(prompt)
        if match is None:
            return self.scenario, prompt
        scenario = cast(Scenario, match.group(1))
        return scenario, _SCENARIO_DIRECTIVE.sub("", prompt, count=1)

    def _response_for(self, scenario: Scenario, prompt: str) -> str:
        """Формирует строку ответа для заданного сценария."""
        if scenario == "success":
            digest = hashlib.sha256(f"{self.seed}:{prompt}".encode()).hexdigest()[:16]
            return f"MOCK_OK[{digest}]: детерминированный ответ мок-провайдера"
        return _SCENARIO_RESPONSES[scenario]

    def _record(self, kind: str, scenario: Scenario, prompt: str, params: dict[str, Any]) -> None:
        self.calls.append({"kind": kind, "scenario": scenario, "prompt": prompt, "params": params})

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Возвращает детерминированный ответ по (seed, prompt) или сценарий."""
        scenario, clean_prompt = self._resolve(prompt)
        self._record("generate", scenario, clean_prompt, kwargs)
        return self._response_for(scenario, clean_prompt)

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Стримит ответ кусками по 16 символов (детерминизм как у generate)."""
        scenario, clean_prompt = self._resolve(prompt)
        self._record("stream", scenario, clean_prompt, kwargs)
        response = self._response_for(scenario, clean_prompt)
        for start in range(0, len(response), 16):
            yield response[start : start + 16]

    async def embeddings(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Возвращает единичные векторы длины 1536, детерминированные по (seed, текст)."""
        self.calls.append({"kind": "embeddings", "count": len(texts), "params": kwargs})
        vectors: list[list[float]] = []
        for item in texts:
            rng = random.Random(f"{self.seed}:{item}")
            vector = [rng.uniform(-1.0, 1.0) for _ in range(_EMBEDDING_DIM)]
            norm = sum(value**2 for value in vector) ** 0.5
            vectors.append([value / norm for value in vector])
        return vectors
