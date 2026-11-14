"""Researcher agent node (T058).

Extracts territory, period, and analysis type from user query text using LLM.
Continues workflow with available data if extraction is incomplete.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from glist.llm import get_llm_provider
from glist.llm.provider import LLMProvider

logger = logging.getLogger("glist.agents.researcher")

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_YEARS = re.compile(r"(\d+)\s*(?:год|года|лет)", re.IGNORECASE)
_LAST_YEAR = re.compile(
    r"(?:последн\w*|прошедш\w*)\s+год|за\s+(?:последний\s+)?год",
    re.IGNORECASE,
)
_CITY_AFTER_PREP = re.compile(
    r"\b(?:в|во)\s+([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)\s+(?:за|с|по|на)\b"
)
_MOSCOW = re.compile(r"москв", re.IGNORECASE)


def _extract_json(text: str) -> dict[str, Any]:
    """Извлекает первый JSON-объект из ответа LLM (в т.ч. из markdown)."""
    cleaned = text.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```")
    cleaned = cleaned.removesuffix("```").strip()
    candidates = [cleaned]
    match = _JSON_OBJECT.search(cleaned)
    if match is not None:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    return {}


def _heuristic_params(query_text: str) -> dict[str, Any]:
    """Резервное извлечение параметров, если LLM вернул невалидный JSON."""
    now = datetime.now(UTC)
    match = _YEARS.search(query_text)
    if _LAST_YEAR.search(query_text) is not None:
        years = 1
    elif match is not None:
        years = int(match.group(1))
    else:
        years = 5
    years = max(1, min(years, 30))
    start = (now - timedelta(days=365 * years)).date().isoformat()
    city_match = _CITY_AFTER_PREP.search(query_text)
    if _MOSCOW.search(query_text) is not None:
        name = "Москва"
    elif city_match is not None:
        name = city_match.group(1).strip()
    else:
        name = "Территория запроса"
    return {
        "territories": [{"name": name}],
        "period": {"start": start, "end": now.date().isoformat()},
        "analysis_type": "vegetation_change",
    }


def _iso_date(value: object) -> date | None:
    """Разбирает ISO-дату из ответа LLM (иначе None)."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _build_prompt(query_text: str) -> str:
    """Строгий промпт: только JSON по схеме, без markdown и пояснений."""
    return (
        "Ты — геоаналитик. Извлеки из вопроса параметры анализа и верни СТРОГО "
        "один валидный JSON-объект без markdown, комментариев и текста вокруг.\n"
        "Схема ответа:\n"
        '{"territories":[{"name":"строка","description":"строка"}],'
        '"period":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"},'
        '"analysis_type":"vegetation_change"}\n'
        "Правила: период — ISO-даты; если сказано «за N лет», end = сегодня, "
        "start = сегодня минус N лет; территории бери из текста вопроса; "
        "analysis_type всегда vegetation_change.\n"
        f"Вопрос: {query_text}"
    )


async def researcher_node(
    state: dict[str, Any],
    llm: LLMProvider | None = None,
) -> dict[str, Any]:
    """Researcher node: извлекает территорию, период и тип анализа.

    Если провайдер не передан явно, используется ``get_llm_provider()``.
    Невалидный JSON или ошибка Ollama не останавливают граф — применяются
    эвристики по тексту запроса.
    """
    query_text = str(state.get("query_text", "")).strip()
    if not query_text:
        return {"next_agent": "validator", "messages": ["Researcher: no query text"]}

    provider = llm or get_llm_provider()
    parsed: dict[str, Any] = {}
    try:
        response = await provider.generate(_build_prompt(query_text), temperature=0.0)
        parsed = _extract_json(response)
    except Exception as exc:  # noqa: BLE001 — ошибка LLM не должна ронять граф
        logger.warning("Researcher: ошибка LLM: %s", exc)

    if not parsed:
        logger.warning("Researcher: LLM вернул невалидный JSON, применяю эвристики")
        parsed = _heuristic_params(query_text)

    context = dict(state.get("context") or {})
    heuristic = _heuristic_params(query_text)

    raw_territories = parsed.get("territories")
    valid_territories: list[dict[str, Any]] = []
    if isinstance(raw_territories, list):
        for item in raw_territories:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            valid_territories.append({**item, "name": name})
    if valid_territories:
        context["territories"] = valid_territories
    elif not context.get("territories"):
        logger.warning("Researcher: LLM не вернул территорию, применяю эвристику")
        context["territories"] = heuristic["territories"]

    period = parsed.get("period")
    start_raw = period.get("start") if isinstance(period, dict) else None
    end_raw = period.get("end") if isinstance(period, dict) else None
    start_date = _iso_date(start_raw)
    end_date = _iso_date(end_raw)
    relative_period = (
        _LAST_YEAR.search(query_text) is not None
        or _YEARS.search(query_text) is not None
    )
    if relative_period:
        # «За последний год / за N лет» считается от текущей даты, а не по
        # произвольным датам, которые может вернуть LLM.
        context.setdefault("period", {}).update(heuristic["period"])
    elif start_date is not None and end_date is not None and start_date < end_date:
        context.setdefault("period", {})["start"] = start_date.isoformat()
        context.setdefault("period", {})["end"] = end_date.isoformat()
    else:
        logger.warning("Researcher: LLM не вернул корректный период, применяю эвристику")
        context.setdefault("period", {}).update(heuristic["period"])

    analysis_type = parsed.get("analysis_type")
    if isinstance(analysis_type, str) and analysis_type:
        context["analysis_type"] = analysis_type

    return {
        "context": context,
        "next_agent": "analyst",
        "messages": ["Researcher: параметры извлечены"],
    }