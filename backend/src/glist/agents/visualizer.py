"""Visualizer agent node (T060).

Generates text explanation via LLM and collects GeoJSON via export_geojson.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import logging
from typing import Any

from glist.llm import get_llm_provider
from glist.llm.provider import LLMProvider

logger = logging.getLogger("glist.agents.visualizer")


async def visualizer_node(
    state: dict[str, Any],
    llm: LLMProvider | None = None,
) -> dict[str, Any]:
    """Visualizer node: формирует текстовое объяснение результата.

    Если провайдер не передан явно, используется ``get_llm_provider()``.
    При ошибке LLM остаётся детерминированное объяснение.
    """
    context = dict(state.get("context") or {})
    change_zones = context.get("change_zones") or []
    territories = context.get("territories") or []
    if not isinstance(change_zones, list) or not change_zones:
        return {"next_agent": "validator"}

    territory_names = [
        str(item.get("name", "Территория"))
        for item in territories[:3]
        if isinstance(item, dict)
    ]
    names_text = ", ".join(territory_names) or "Москва"
    counts = {
        change_class: sum(
            1
            for zone in change_zones
            if isinstance(zone, dict)
            and zone.get("change_class") == change_class
        )
        for change_class in ("retained", "lost", "new")
    }
    coverage = context.get("coverage") or {}
    shared = coverage.get("sharedValid") if isinstance(coverage, dict) else None
    diagnostics = context.get("analysis_diagnostics") or {}
    limitations = (
        diagnostics.get("limitations", [])
        if isinstance(diagnostics, dict)
        else []
    )
    summary = (
        f"Потеряно зон: {counts['lost']}, новые зоны: {counts['new']}, "
        f"сохранено (не изменение): {counts['retained']}. "
        + (
            f"Валидное покрытие обеих дат: {float(shared) * 100:.1f}%."
            if isinstance(shared, (int, float))
            else "Валидное покрытие не рассчитано."
        )
    )
    if limitations:
        summary += " Ограничения: " + "; ".join(str(item) for item in limitations)
    explanation = summary

    provider = llm or get_llm_provider()
    prompt = (
        "Ты — геоаналитик. Кратко (2–4 предложения) объясни пользователю "
        "результат анализа растительности. Без markdown. Используй только "
        "переданные числа и ограничения, ничего не выдумывай. Данные: "
        f"территории={names_text}; зоны по классам={counts}; "
        f"валидное покрытие={shared}; ограничения={limitations}; "
        f"формула NDVI=(B08-B04)/(B08+B04); "
        f"сезоны={diagnostics.get('seasons') if isinstance(diagnostics, dict) else None}; "
        f"статистика={context.get('aggregate', {})}."
    )
    try:
        generated = await provider.generate(prompt, temperature=0.2)
        if generated.strip():
            explanation = generated.strip()
    except Exception as exc:  # noqa: BLE001 — ошибка LLM не должна ронять граф
        logger.warning("Visualizer: ошибка LLM: %s", exc)

    context["text_explanation"] = explanation
    visualization = {
        "text_explanation": explanation,
        "summary_text": summary,
        "change_zones": len(change_zones),
    }
    return {
        "context": context,
        "visualization": visualization,
        "next_agent": "validator",
    }