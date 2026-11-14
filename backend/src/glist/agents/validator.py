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
    sufficient = context.get("analysis_sufficient")
    diagnostics = context.get("analysis_diagnostics") or {}
    raw_limitations = (
        diagnostics.get("limitations") if isinstance(diagnostics, dict) else None
    )
    limitations = (
        [str(item) for item in raw_limitations]
        if isinstance(raw_limitations, list)
        else []
    )
    analysis_error = context.get("analysis_error")
    failure = context.get("analysis_failure")
    failure_category = (
        str(failure.get("category")) if isinstance(failure, dict) else None
    )

    if not change_zones:
        reasons: list[str] = []
        if isinstance(analysis_error, str) and analysis_error.strip():
            reasons.append(analysis_error.strip())
        reasons.extend(limitations)
        if not reasons:
            reasons.append("No change zones detected")
        category = failure_category or (
            "analysis_error" if sufficient else "data_quality"
        )
        return {
            "validation": {
                "outcome": "passed" if sufficient else "failed",
                "category": category,
                "attempt_no": attempt_no,
                "reasons": [] if sufficient else reasons,
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

    # Все зоны структурно валидны. Технический успех расчёта не равен
    # пригодности данных для интерпретации: при ограничениях качества
    # результат отклоняется с категорией data_quality и списком ограничений.
    if sufficient is False:
        reasons = limitations or [
            "Недостаточно пригодных данных для интерпретации изменений"
        ]
        return {
            "validation": {
                "outcome": "failed",
                "category": "data_quality",
                "attempt_no": attempt_no,
                "reasons": reasons,
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