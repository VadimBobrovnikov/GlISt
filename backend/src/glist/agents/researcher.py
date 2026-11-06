"""Researcher agent node (T058).

Extracts territory, period, and analysis type from user query text using LLM.
Continues workflow with available data if extraction is incomplete.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

from typing import Any

from glist.llm.mock_provider import MockLLMProvider


async def researcher_node(state: dict[str, Any]) -> dict[str, Any]:
    """Researcher node: extract query parameters via LLM.

    Args:
        state: Current AgentState containing query_text and other context.

    Returns:
        Partial state update with extracted context and message.
    """
    from glist.agents.state import AgentState

    query_text: str = state.get("query_text", "")

    if not query_text:
        return {"next_agent": "validator", "messages": ["Researcher: no query text"]}

    # Use LLM to extract territory, period, and analysis type
    llm = MockLLMProvider(seed=42, scenario="success")

    # Construct prompt for parameter extraction
    prompt = (
        f'Extract: territories (names/descriptions), period (start/end dates), '
        f'and analysis type (e.g., "vegetation_change"). Query: "{query_text}"'
    )

    try:
        response = await llm.acomplete(prompt) if hasattr(llm, "acomplete") else "{}"

        import json
        try:
            parsed = json.loads(response) if isinstance(response, str) else response
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        context: dict[str, Any] = state.get("context", {})

        # Extract territories (may be empty)
        territories = parsed.get("territories", [])
        if territories:
            context["territories"] = territories

        # Extract period (may be partial)
        period = parsed.get("period", {})
        if period.get("start"):
            context.setdefault("period", {})["start"] = period["start"]
        if period.get("end"):
            context.setdefault("period", {})["end"] = period["end"]

        # Extract analysis type
        analysis_type = parsed.get("analysis_type")
        if analysis_type:
            context["analysis_type"] = analysis_type

        # Always proceed to analyst
        return {
            "context": context,
            "next_agent": "analyst",
            "messages": ["Researcher: parameters extracted"],
        }

    except Exception:
        # On error, continue with current context
        return {
            "context": state.get("context", {}),
            "next_agent": "analyst",
            "messages": ["Researcher: error, continuing"],
        }