"""Validator agent node (T061).

Validates that change zones are valid, checks for data quality issues,
and sets the outcome (passed/failed) in the state.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

from typing import Any


async def validator_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator node: validate change zones and set outcome.

    Args:
        state: Current state containing change_zones, datasets, etc.

    Returns:
        Partial state update with validation outcome and next_agent.
    """
    context: dict[str, Any] = state.get("context", {})
    change_zones: list[dict[str, Any]] = context.get("change_zones", [])
    attempt_no: int = state.get("attempt_count", 0)

    if not change_zones:
        sufficient = bool(context.get("analysis_sufficient"))
        return {
            "validation": {
                "outcome": "passed" if sufficient else "failed",
                "category": "analysis_error" if sufficient else "data_quality",
                "attempt_no": attempt_no,
                "reasons": [] if sufficient else ["No change zones detected"],
                "fixes_applied": [],
            },
            "next_agent": "finalize",
        }

    # Validate change zones - check required fields
    validation_errors: list[str] = []
    valid_classes: set[str] = {"retained", "lost", "new"}

    for i, zone in enumerate(change_zones):
        if isinstance(zone, dict):
            change_class: str = zone.get("change_class", "")
            area_km2: float = zone.get("area_km2", 0.0)

            if change_class not in valid_classes:
                validation_errors.append(
                    f"Zone {i}: invalid change_class '{change_class}'"
                )

            if not isinstance(area_km2, (int, float)) or area_km2 < 0:
                validation_errors.append(
                    f"Zone {i}: invalid area_km2 value {area_km2}"
                )

    # If validation errors found
    if validation_errors:
        return {
            "validation": {
                "outcome": "failed",
                "category": "analysis_error",
                "attempt_no": attempt_no,
                "reasons": validation_errors,
                "fixes_applied": [],
            },
            "next_agent": "finalize",
        }

    # All zones valid
    return {
        "validation": {
            "outcome": "passed",
            "category": "analysis_error",
            "attempt_no": attempt_no,
            "reasons": [],
            "fixes_applied": [],
        },
        "next_agent": "finalize",
    }