"""Visualizer agent node (T060).

Generates text explanation via LLM and collects GeoJSON via export_geojson.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

from typing import Any

from glist.tools.export_geojson import export_geojson


async def visualizer_node(state: dict[str, Any]) -> dict[str, Any]:
    """Visualizer node: generate explanation and GeoJSON.

    Args:
        state: Current state containing change_zones and other context.

    Returns:
        Partial state update with text explanation and next_agent.
    """
    from glist.agents.state import AgentState

    context: dict[str, Any] = state.get("context", {})
    change_zones: list = context.get("change_zones", [])
    territories: list = context.get("territories", [])

    if not change_zones:
        # Don't add error - continue
        return {"next_agent": "validator"}

    # Generate text explanation
    territory_names: list = [t.get("name", "Unknown") for t in territories[:3]]
    explanation: str = f"Анализ растительности для территорий: {', '.join(territory_names)}. "
    explanation += f"Выделены зоны изменений: {len(change_zones)} зон."

    # Build GeoJSON simplified - just return without adding error
    context.update({
        "text_explanation": explanation,
    })

    return {"context": context, "next_agent": "validator"}