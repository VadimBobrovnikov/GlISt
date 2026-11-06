"""Graph assembly for LangGraph multi-agent workflow (T041/T058-T062).

Assembles the full workflow graph: Researcher -> Analyst -> Visualizer -> Validator -> Finalize.
Communicates only through the shared AgentState.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END

from glist.agents.researcher import researcher_node
from glist.agents.analyst import analyst_node
from glist.agents.visualizer import visualizer_node
from glist.agents.validator import validator_node


def route_after_validator(state: dict[str, Any]) -> str:
    """Route after validator node extracts outcome and category.

    Returns only the string name of the next agent node.
    """
    validation: dict[str, Any] = state.get("validation", {})
    outcome: str = validation.get("outcome", "failed")
    category: str = validation.get("category", "data_quality")
    attempt_count: int = state.get("attempt_count", 0)

    # Logic for routing
    if outcome == "passed":
        return "finalize"
    if outcome == "failed" and category == "analysis_error" and attempt_count < 1:
        return "analyst"
    # All other cases: proceed to finalize
    return "finalize"


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Finalize node: mark workflow as completed."""
    messages: list = state.get("messages", [])
    messages.append("Finalize: завершено")

    return {
        "messages": messages,
        "next_agent": "END",
        "errors": state.get("errors", []),
    }


def build_graph() -> Any:
    """Build and return the full LangGraph workflow graph."""
    from langgraph.graph import StateGraph

    # Initialize graph with AgentState schema
    workflow = StateGraph(dict)

    # Add all nodes
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("analyst", analyst_node)
    workflow.add_node("visualizer", visualizer_node)
    workflow.add_node("validator", validator_node)
    workflow.add_node("finalize", finalize_node)

    # Add edges
    workflow.set_entry_point("researcher")

    workflow.add_edge("researcher", "analyst")
    workflow.add_edge("analyst", "visualizer")
    workflow.add_edge("visualizer", "validator")
    # Use conditional edge from validator using route_after_validator
    workflow.add_conditional_edges("validator", route_after_validator)
    workflow.add_edge("finalize", END)

    return workflow.compile()