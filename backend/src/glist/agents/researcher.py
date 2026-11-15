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
from glist.tools.geocode import normalize_name, normalize_object_type

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
_CITY_IN_DISTRICT = re.compile(
    r"([А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?)"
    r"\s+в\s+районе\s+([А-ЯЁ][А-Яа-яЁё\-]+)"
)
_CITY_NEAR_AREA = re.compile(
    r"(?:зон\w*|растительност\w*|территори\w*)"
    r"\s+([А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?)"
    r"(?=\s+(?:за|с|по|на)\b)"
)
_DISTRICT_IN_CITY = re.compile(
    r"район[е]?\s+([А-ЯЁ][А-Яа-яЁё\-]+)"
    r"\s+(?:в|города)\s+([А-ЯЁ][А-Яа-яЁё\-]+)"
)
_OBJECT_TYPE_RE = re.compile(
    r"(микрорайон|район|квартал|остров|парк)\s+"
    r"([А-ЯЁA-Z0-9][\w\-]*(?:\s+[А-ЯЁA-Z0-9][\w\-]*)?)",
    re.IGNORECASE,
)
_PARENT_RE = re.compile(
    r"(?:в|во|города)\s+([А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?)"
)
_CITY_AFTER_IN = re.compile(
    r"\bв\s+([А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?)"
)
_GENERIC_TERRITORY_NAMES = frozenset(
    {
        "зеленая зона",
        "зеленые зоны",
        "зелень",
        "растительность",
        "территория",
        "территория запроса",
        "город",
        "район",
        "парк",
        "место",
    }
)
_OBJECT_TYPE_WORDS = {
    "район": "district",
    "микрорайон": "microdistrict",
    "квартал": "quarter",
    "остров": "island",
    "парк": "park",
}


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


def _district_and_city(query_text: str) -> tuple[str | None, str | None]:
    """Извлекает район и родительский город из текста запроса."""
    match = _CITY_IN_DISTRICT.search(query_text)
    if match is not None:
        return match.group(2).strip(), match.group(1).strip()
    match = _DISTRICT_IN_CITY.search(query_text)
    if match is not None:
        return match.group(1).strip(), match.group(2).strip()
    return None, None


def _territory_from_text(query_text: str) -> dict[str, Any]:
    """Общий разбор территории: тип из ключевого слова, имя и город."""
    district, city = _district_and_city(query_text)
    if district is not None:
        territory: dict[str, Any] = {"name": district, "type": "district"}
        if city:
            territory["parent"] = city
        return territory

    type_match = _OBJECT_TYPE_RE.search(query_text)
    if type_match is not None:
        kind = _OBJECT_TYPE_WORDS.get(type_match.group(1).lower(), "other")
        name = type_match.group(2).strip()
        territory = {"name": name, "type": kind}
        parent_match = _PARENT_RE.search(query_text, type_match.end())
        if parent_match is not None and parent_match.group(1).strip() != name:
            territory["parent"] = parent_match.group(1).strip()
        return territory

    city_match = (
        _CITY_AFTER_PREP.search(query_text)
        or _CITY_NEAR_AREA.search(query_text)
        or _CITY_AFTER_IN.search(query_text)
    )
    if city_match is not None:
        return {"name": city_match.group(1).strip(), "type": "city"}
    return {"name": "", "type": "other"}


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
    territory = _territory_from_text(query_text)
    return {
        "territories": [territory],
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


def _territory_prompt(query_text: str) -> str:
    return (
        "Ты — геоаналитик. Извлеки объект территории из вопроса и верни "
        "СТРОГО один JSON-объект без markdown и комментариев.\n"
        "Схема:\n"
        '{"name":"название объекта","normalizedName":"нормализованное",'
        '"type":"city|district|microdistrict|quarter|island|park|other",'
        '"parent":"город или null"}\n'
        "Правила: если назван район, микрорайон, квартал, остров или парк — "
        "укажи его в name, а город — в parent; для города parent=null. "
        "Исправляй падеж и очевидные опечатки, но не выдумывай отсутствующие "
        "названия и города. type определяй по смыслу объекта.\n"
        f"Вопрос: {query_text}"
    )


def _validated_territory(
    parsed: dict[str, Any],
    heuristic: dict[str, Any],
) -> dict[str, Any] | None:
    name = str(parsed.get("name") or "").strip()
    if not name and heuristic.get("name"):
        name = str(heuristic.get("name") or "").strip()
    if not name:
        return None
    raw_type = parsed.get("type")
    object_type = (
        normalize_object_type(str(raw_type))
        if raw_type is not None
        else "other"
    )
    parent = parsed.get("parent")
    parent_name = (
        parent.strip() if isinstance(parent, str) and parent.strip() else None
    )
    if isinstance(parent_name, str) and parent_name.lower() in {
        "null",
        "none",
        "нет",
        "-",
    }:
        parent_name = None
    if normalize_object_type(object_type) == "city":
        parent_name = None
    heuristic_type = normalize_object_type(
        str(heuristic.get("type") or "other")
    )
    heuristic_name = str(heuristic.get("name") or "")
    heuristic_parent = heuristic.get("parent")
    heuristic_node = normalize_name(heuristic_name)
    parsed_node = normalize_name(name)
    generic_name = parsed_node in _GENERIC_TERRITORY_NAMES
    parent_truncated = (
        isinstance(parent_name, str)
        and bool(heuristic_node)
        and heuristic_node.startswith(normalize_name(parent_name))
        and normalize_name(parent_name) != ""
    )
    if heuristic_name and heuristic_type != "other" and (
        generic_name or parent_truncated
    ):
        name = heuristic_name
        object_type = heuristic_type
        parent_name = (
            heuristic_parent
            if isinstance(heuristic_parent, str) and heuristic_parent.strip()
            else parent_name
        )
    same_place = (
        bool(heuristic_name)
        and normalize_name(heuristic_name) == normalize_name(name)
    )
    if object_type == "other" and heuristic_type != "other":
        object_type = heuristic_type
        parent_name = parent_name or (
            heuristic_parent if isinstance(heuristic_parent, str) else None
        )
    if object_type in ("city", "other") and heuristic_type not in (
        "city",
        "other",
    ):
        object_type = heuristic_type
        parent_name = parent_name or (
            heuristic_parent if isinstance(heuristic_parent, str) else None
        )
    if same_place and not parent_name and isinstance(heuristic_parent, str):
        parent_name = heuristic_parent
    if object_type == "city" or (
        parent_name is not None
        and normalize_name(parent_name) == normalize_name(name)
    ):
        parent_name = None
    normalized = normalize_name(name)
    return {
        "name": name,
        "normalizedName": normalized,
        "type": object_type,
        "parent": parent_name,
    }


async def extract_territory_params(
    query_text: str,
    llm: LLMProvider | None = None,
) -> dict[str, Any] | None:
    """Извлекает объект/тип/город; LLM предлагает, код проверяет."""
    provider = llm or get_llm_provider()
    parsed: dict[str, Any] = {}
    try:
        response = await provider.generate(
            _territory_prompt(query_text),
            temperature=0.0,
        )
        parsed = _extract_json(response)
    except Exception as exc:  # noqa: BLE001 — ошибка LLM не должна ронять resolve
        logger.warning("Researcher: ошибка извлечения территории: %s", exc)
    heuristic = _territory_from_text(query_text)
    candidate = _validated_territory(parsed, heuristic)
    if candidate is not None:
        return candidate
    if heuristic.get("name"):
        return {
            "name": str(heuristic["name"]),
            "normalizedName": normalize_name(str(heuristic["name"])),
            "type": normalize_object_type(str(heuristic.get("type") or "other")),
            "parent": heuristic.get("parent"),
        }
    return None


def _build_prompt(query_text: str) -> str:
    """Строгий промпт: только JSON по схеме, без markdown и пояснений."""
    return (
        "Ты — геоаналитик. Извлеки из вопроса параметры анализа и верни СТРОГО "
        "один валидный JSON-объект без markdown, комментариев и текста вокруг.\n"
        "Схема ответа:\n"
        '{"territories":[{"name":"строка","parent":"город или null",'
        '"type":"district|city","description":"строка"}],'
        '"period":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"},'
        '"analysis_type":"vegetation_change"}\n'
        "Правила: период — ISO-даты; если сказано «за N лет», end = сегодня, "
        "start = сегодня минус N лет; если в вопросе назван район или "
        "микрорайон, верни его в name, а город — в parent, type=district; "
        "иначе name — город, type=city; "
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
            valid_territories.append(
                {
                    **item,
                    "name": name,
                    "type": normalize_object_type(str(item.get("type") or "city")),
                    "parent": (
                        str(item.get("parent")).strip()
                        if isinstance(item.get("parent"), str)
                        and str(item.get("parent")).strip()
                        else None
                    ),
                }
            )
    heuristic_territory = heuristic["territories"][0]
    heuristic_kind = normalize_object_type(
        str(heuristic_territory.get("type") or "other")
    )
    if heuristic_kind not in ("city", "other"):
        # Конкретный объект из текста важнее ответа LLM, который часто
        # оставляет только город.
        context["territories"] = [heuristic_territory]
    elif valid_territories:
        context["territories"] = valid_territories
    elif not context.get("territories"):
        logger.warning("Researcher: LLM не вернул территорию, применяю эвристику")
        context["territories"] = heuristic["territories"]

    resolved = context.get("resolved_territory")
    if isinstance(resolved, dict) and resolved.get("name"):
        context["territories"] = [resolved]

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