"""Контрольный запуск анализа на сопоставимых сезонных данных (локально).

Создаёт query/analysis_run в рабочей БД и выполняет Analyst→Visualizer→Validator
на реальных STAC-сценах; сохраняет результат теми же функциями, что ARQ-задача.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(r"d:\Проекты_Мага\GlISt")
sys.path.insert(0, str(PROJECT / "backend" / "src"))

for line in (PROJECT / ".env").read_text(encoding="utf-8").splitlines():
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        continue
    key, value = text.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from glist.agents.analyst import analyst_node  # noqa: E402
from glist.agents.validator import validator_node  # noqa: E402
from glist.agents.visualizer import visualizer_node  # noqa: E402
from glist.db.models import AnalysisRun, Query  # noqa: E402
from glist.db.session import get_session_ctx  # noqa: E402
from glist.jobs.analyze_run import _save_result  # noqa: E402
from glist.services.run_service import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_FAILED_VALIDATION,
    STATUS_PROCESSING,
    STATUS_WAITING,
)

TEMPLATE_RUN = uuid.UUID("0e42d367-7ed5-48db-b4cc-1a832b43a2f0")
QUERY_TEXT = (
    "Проанализируй изменение растительности в Москве "
    "с 2019-01-01 по 2023-04-14"
)
PERIOD = {"start": "2019-01-01", "end": "2023-04-14"}


async def _merge(state: dict, update: dict) -> dict:
    merged = {**state, **update}
    merged["context"] = {
        **(state.get("context") or {}),
        **(update.get("context") or {}),
    }
    return merged


async def main() -> None:
    async with get_session_ctx() as session:
        template = await session.get(AnalysisRun, TEMPLATE_RUN)
        if template is None:
            raise SystemExit("template run not found")
        template_query = await session.get(Query, template.query_id)
        if template_query is None:
            raise SystemExit("template query not found")
        user_id = template_query.user_id
        settings_hash = template.settings_hash
        seed = template.seed
        rag_version = template.rag_index_version
        model_versions = template.model_versions

    params_hash = hashlib.sha256(
        f"control-{uuid.uuid4()}-{QUERY_TEXT}".encode()
    ).hexdigest()

    async with get_session_ctx() as session:
        query = Query(
            user_id=user_id,
            text=QUERY_TEXT,
            language="ru",
            query_kind="primary",
            status=STATUS_WAITING,
        )
        session.add(query)
        await session.flush()
        run = AnalysisRun(
            query_id=query.id,
            status=STATUS_WAITING,
            queued_at=datetime.now(UTC),
            trace_id=f"control-{uuid.uuid4().hex}",
            settings_hash=settings_hash,
            params_hash=params_hash,
            seed=seed,
            rag_index_version=rag_version,
            model_versions=model_versions,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        query_id = query.id
        await session.commit()

    print("RUN_ID", run_id, flush=True)

    async with get_session_ctx() as session:
        row = await session.get(AnalysisRun, run_id)
        row.status = STATUS_PROCESSING
        row.started_at = datetime.now(UTC)
        await session.commit()

    state: dict = {
        "run_id": str(run_id),
        "query_id": str(query_id),
        "query_text": QUERY_TEXT,
        "language": "ru",
        "mode": "single",
        "next_agent": "researcher",
        "attempt_count": 0,
        "context": {
            "territories": [{"name": "Москва"}],
            "period": dict(PERIOD),
            "ndvi_threshold": 0.25,
            "min_area_m2": 1000.0,
        },
        "datasets": {},
        "analysis_result": None,
        "visualization": None,
        "validation": None,
        "errors": [],
        "messages": [],
    }

    state = await _merge(state, await analyst_node(state))
    diagnostics = state["context"].get("analysis_diagnostics") or {}
    print(
        "PERIOD_SELECTION",
        json.dumps(diagnostics.get("periodSelection"), ensure_ascii=True)[:1200],
        flush=True,
    )
    print("ACTUAL_DATES", json.dumps(diagnostics.get("actualDates")), flush=True)
    print(
        "SEASONS",
        json.dumps(diagnostics.get("seasons"), ensure_ascii=True),
        flush=True,
    )
    print("COVERAGE", json.dumps(diagnostics.get("coverage")), flush=True)
    print(
        "GRID_CHECK",
        json.dumps(diagnostics.get("grid", {}).get("check")),
        flush=True,
    )
    print(
        "GRID_ALIGNMENT",
        diagnostics.get("grid", {}).get("alignment"),
        flush=True,
    )
    print(
        "INTERPRETABILITY",
        json.dumps(diagnostics.get("interpretability"), ensure_ascii=True),
        flush=True,
    )
    print(
        "LIMITATIONS",
        json.dumps(diagnostics.get("limitations"), ensure_ascii=True),
        flush=True,
    )
    print("ZONES", len(state["context"].get("change_zones") or []), flush=True)

    if state.get("next_agent") == "visualizer":
        state = await _merge(state, await visualizer_node(state))
    state = await _merge(state, await validator_node(state))
    validation = state.get("validation") or {}
    print("VALIDATION", json.dumps(validation, ensure_ascii=True), flush=True)

    passed = str(validation.get("outcome")) == "passed"
    async with get_session_ctx() as session:
        run = await session.get(AnalysisRun, run_id)
        await _save_result(session, run, state)
        run.finished_at = datetime.now(UTC)
        run.status = STATUS_COMPLETED if passed else STATUS_FAILED_VALIDATION
        run.rejection_category = (
            None if passed else str(validation.get("category") or "analysis_error")
        )
        reasons = [str(item) for item in (validation.get("reasons") or [])]
        run.status_reason = "; ".join(reasons)[:1000] or None
        query_row = await session.get(Query, run.query_id)
        query_row.status = run.status
        await session.commit()

    print("FINAL_STATUS", run.status, flush=True)


asyncio.run(main())
