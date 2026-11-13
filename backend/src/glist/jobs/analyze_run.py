"""Фоновая задача выполнения анализа (задача T033).

Запускает скомпилированный граф LangGraph для заданного run_id,
публикует события в Redis через EventPublisher и обновляет статус в БД.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.agents.graph import build_graph
from glist.agents.state import AgentState
from glist.db.models import (
    AnalysisResult,
    AnalysisRun,
    AreaOfInterest,
    ChangeZone,
    Query,
    ResultTerritory,
    SettingsProfile,
)
from glist.db.session import get_session_ctx
from glist.services.run_service import (
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_FAILED_VALIDATION,
    STATUS_PROCESSING,
)
from glist.trace.events import EventPublisher

logger = logging.getLogger("glist.jobs.analyze_run")


def _as_dict(value: object) -> dict[str, Any]:
    """Приводит значение к JSON-объекту со строковыми ключами."""
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_list(value: object) -> list[Any]:
    """Приводит значение к списку."""
    if isinstance(value, list):
        return value
    return []


def _as_int(value: object) -> int:
    """Приводит значение к целому числу (иначе 0)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _first_text(*values: object) -> str:
    """Возвращает первый непустой текст из аргументов."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Анализ завершён."


def _rejection_reason(value: object) -> str:
    """Собирает текст причины отклонения для поля status_reason."""
    reasons = [str(item) for item in _as_list(value)]
    if reasons:
        return "; ".join(reasons)[:1000]
    return "Результат не прошёл проверку"


async def analyze_run(ctx: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Основная задача ARQ: граф, сохранение AnalysisResult и статуса запуска.

    Args:
        ctx: Контекст ARQ.
        run_id: Идентификатор запуска analysis_run в БД.

    Returns:
        Результат выполнения со статусом и сообщениями графа.
    """
    publisher = EventPublisher()
    run_uuid = uuid.UUID(run_id)

    try:
        await publisher.publish(
            run_id=run_id,
            event="run.started",
            payload={"run_id": run_id},
        )

        query_text = ""
        query_id = ""
        language = "ru"
        analysis_context: dict[str, Any] = {}

        # Переводим запуск «в обработке» и читаем текст запроса для графа.
        async with get_session_ctx() as session:
            run = await session.get(AnalysisRun, run_uuid)
            if run is None:
                logger.warning("analysis_run %s не найден", run_id)
                return {"status": "missing", "run_id": run_id}
            query = await session.get(Query, run.query_id)
            if query is not None:
                query_text = query.text
                query_id = str(query.id)
                language = query.language
            else:
                query_id = str(run.query_id)
            profile = await session.get(SettingsProfile, run.settings_hash)
            if profile is not None and isinstance(profile.values, dict):
                threshold = profile.values.get("ndvi_threshold")
                if threshold is not None:
                    analysis_context["ndvi_threshold"] = threshold
                min_area = profile.values.get("min_area_m2")
                if min_area is not None:
                    analysis_context["min_area_m2"] = min_area
            run.status = STATUS_PROCESSING
            run.started_at = datetime.now(UTC)
            await session.commit()

        initial_state: AgentState = {
            "run_id": run_id,
            "query_id": query_id,
            "query_text": query_text,
            "language": language,
            "mode": "single",
            "next_agent": "researcher",
            "attempt_count": 0,
            "context": analysis_context,
            "datasets": {},
            "analysis_result": None,
            "visualization": None,
            "validation": None,
            "errors": [],
            "messages": [],
        }

        # Узлы графа используют MockLLMProvider напрямую — Ollama не требуется.
        graph = build_graph()
        graph_result: dict[str, Any] = await graph.ainvoke(initial_state)

        validation = _as_dict(graph_result.get("validation"))
        passed = str(validation.get("outcome") or "failed") == "passed"

        async with get_session_ctx() as session:
            run = await session.get(AnalysisRun, run_uuid)
            if run is None:
                return {"status": "missing", "run_id": run_id}
            await _save_result(session, run, graph_result)
            if passed:
                run.status = STATUS_COMPLETED
                run.status_reason = None
                run.rejection_category = None
            else:
                category = str(validation.get("category") or "analysis_error")
                run.status = STATUS_FAILED_VALIDATION
                run.rejection_category = (
                    category
                    if category in ("analysis_error", "data_quality")
                    else "analysis_error"
                )
                run.status_reason = _rejection_reason(validation.get("reasons"))
            attempt_no = _as_int(
                validation.get("attempt_no", validation.get("attemptNo", 0))
            )
            run.attempt_count = max(0, min(1, attempt_no))
            run.finished_at = datetime.now(UTC)
            await session.commit()

        await publisher.publish(
            run_id=run_id,
            event="run.completed" if passed else "run.validation.rejected",
            payload={
                "run_id": run_id,
                "status": "completed" if passed else "validation_rejected",
                "next_agent": graph_result.get("next_agent"),
                "errors": graph_result.get("errors", []),
            },
        )

        return {
            "status": "completed" if passed else "validation_failed",
            "run_id": run_id,
            "messages": graph_result.get("messages", []),
            "errors": graph_result.get("errors", []),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка выполнения анализа run_id=%s", run_id)
        try:
            async with get_session_ctx() as session:
                run = await session.get(AnalysisRun, run_uuid)
                if run is not None:
                    run.status = STATUS_ERROR
                    run.status_reason = (str(exc) or "Ошибка выполнения анализа")[:1000]
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось записать статус ошибки run_id=%s", run_id)

        await publisher.publish(
            run_id=run_id,
            event="run.failed",
            payload={"run_id": run_id, "error": str(exc)},
        )

        return {
            "status": "failed",
            "run_id": run_id,
            "error": str(exc),
        }

    finally:
        await publisher.close()


async def _save_result(
    session: AsyncSession,
    run: AnalysisRun,
    graph_result: dict[str, Any],
) -> AnalysisResult | None:
    """Сохраняет AnalysisResult и геозоны из состояния графа."""
    context = _as_dict(graph_result.get("context"))
    analysis = _as_dict(graph_result.get("analysis_result"))
    visualization = _as_dict(graph_result.get("visualization"))
    validation = _as_dict(graph_result.get("validation"))
    change_zones = _as_list(context.get("change_zones"))
    territories = _as_list(context.get("territories"))
    passed = str(validation.get("outcome") or "failed") == "passed"

    summary_text = _first_text(
        analysis.get("summary_text"),
        visualization.get("summary_text"),
        context.get("summary_text"),
        f"Анализ завершён: обнаружено зон изменений — {len(change_zones)}.",
    )
    explanation_text = _first_text(
        analysis.get("explanation_text"),
        visualization.get("explanation_text"),
        context.get("text_explanation"),
        summary_text,
    )

    raw_citations = analysis.get("citations", visualization.get("citations"))
    citations = _as_dict(raw_citations)
    if isinstance(raw_citations, list):
        citations = {"items": raw_citations}

    raw_sources = analysis.get(
        "sources_summary", visualization.get("sources_summary")
    )
    sources_summary = _as_dict(raw_sources)
    if isinstance(raw_sources, list):
        sources_summary = {"items": raw_sources}
    if not sources_summary:
        sources_summary = {"datasets": _as_dict(context.get("datasets"))}

    mode = str(analysis.get("mode") or "single")
    if mode not in ("single", "combined"):
        mode = "single"
    territories_count = max(1, min(5, len(territories)))
    has_overlap = bool(analysis.get("has_overlap", False))
    raw_total_area = analysis.get("total_area_km2")
    total_area_km2 = (
        _as_dict(raw_total_area)
        if isinstance(raw_total_area, dict)
        else {
            "note": "сумма по территориям (с возможным пересечением)",
            "zones": len(change_zones),
        }
    )
    validation_outcome = "passed" if passed else "failed"

    existing = (
        await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.run_id == run.id  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await _clear_territories(session, existing.id)
        existing.mode = mode
        existing.territories_count = territories_count
        existing.has_overlap = has_overlap
        existing.total_area_km2 = total_area_km2
        existing.summary_text = summary_text
        existing.explanation_text = explanation_text
        existing.citations = citations
        existing.sources_summary = sources_summary
        existing.validation_outcome = validation_outcome
        await session.flush()
        await _save_territories(session, existing, run, graph_result)
        return existing

    duplicate = (
        await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.params_hash == run.params_hash  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        # Идемпотентный повтор: результат для таких параметров уже существует.
        run.reused_result_id = duplicate.id
        await session.flush()
        return duplicate

    result_row = AnalysisResult(
        run_id=run.id,
        params_hash=run.params_hash,
        mode=mode,
        territories_count=territories_count,
        has_overlap=has_overlap,
        total_area_km2=total_area_km2,
        summary_text=summary_text,
        explanation_text=explanation_text,
        citations=citations,
        sources_summary=sources_summary,
        validation_outcome=validation_outcome,
    )
    session.add(result_row)
    await session.flush()
    await _save_territories(session, result_row, run, graph_result)
    return result_row


async def _clear_territories(
    session: AsyncSession,
    result_id: uuid.UUID,
) -> None:
    """Удаляет прежние территории/зоны результата (для повторного сохранения)."""
    territory_rows = (
        await session.execute(
            select(ResultTerritory).where(
                ResultTerritory.result_id == result_id  # type: ignore[arg-type]
            )
        )
    ).scalars().all()
    for territory in territory_rows:
        # ChangeZone удаляются каскадом на уровне БД (ON DELETE CASCADE).
        await session.delete(territory)
    await session.flush()


def _zone_geometry(zone: dict[str, Any]) -> dict[str, Any] | None:
    """Возвращает GeoJSON-геометрию зоны, если она задана."""
    geometry = zone.get("geometry")
    if isinstance(geometry, dict) and geometry.get("coordinates"):
        return geometry
    return None


def _zone_geom_expr(zone: dict[str, Any]) -> Any:
    """SQL-выражение PostGIS: валидный MultiPolygon SRID 4326 из GeoJSON зоны."""
    geometry = _zone_geometry(zone)
    if geometry is None:
        raise ValueError("У зоны отсутствует GeoJSON-геометрия")
    return func.ST_Multi(
        func.ST_CollectionExtract(
            func.ST_MakeValid(
                func.ST_SetSRID(
                    func.ST_GeomFromGeoJSON(json.dumps(geometry)),
                    4326,
                )
            ),
            3,
        )
    )


def _period_dates(graph_result: dict[str, Any]) -> tuple[date, date]:
    """Даты периода анализа из состояния графа (fallback — сегодня)."""
    context = _as_dict(graph_result.get("context"))
    period = _as_dict(context.get("period"))

    def _parse(value: object) -> date | None:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    today = date.today()
    start = _parse(period.get("start")) or today
    end = _parse(period.get("end")) or today
    if start > end:
        start, end = end, start
    return start, end


async def _save_territories(
    session: AsyncSession,
    result_row: AnalysisResult,
    run: AnalysisRun,
    graph_result: dict[str, Any],
) -> None:
    """Сохраняет территории и зоны изменений (геометрия — через PostGIS)."""
    context = _as_dict(graph_result.get("context"))
    raw_zones = _as_list(context.get("change_zones"))
    zones = [zone for zone in raw_zones if isinstance(zone, dict)]
    if not zones:
        return

    period_start, period_end = _period_dates(graph_result)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for zone in zones:
        name = str(zone.get("territory_name") or "Москва")
        grouped.setdefault(name, []).append(zone)

    total_area = Decimal("0")
    zones_count = 0
    for position, (name, group) in enumerate(list(grouped.items())[:5], start=1):
        valid = [
            (zone, _zone_geom_expr(zone))
            for zone in group
            if _zone_geometry(zone) is not None
        ]
        if not valid:
            continue

        aoi = AreaOfInterest(
            name=name,
            name_source="generated",
            aoi_type="admin",
            boundary=valid[0][1],
            validity_fixed=True,
            area_km2=Decimal("0"),
        )
        session.add(aoi)
        await session.flush()

        territory = ResultTerritory(
            result_id=result_row.id,
            aoi_id=aoi.id,
            position=position,
            territory_name=name,
            retained_area_km2=Decimal("0"),
            lost_area_km2=Decimal("0"),
            new_area_km2=Decimal("0"),
            source_run_id=run.id,
        )
        session.add(territory)
        await session.flush()

        for zone, geom_expr in valid:
            change_class = str(zone.get("change_class") or "")
            if change_class not in ("retained", "lost", "new"):
                continue
            area_m2_expr = func.ST_Area(cast(geom_expr, Geography))
            session.add(
                ChangeZone(
                    result_territory_id=territory.id,
                    change_class=change_class,
                    geom=geom_expr,
                    area_m2=area_m2_expr,  # type: ignore[arg-type]
                    area_km2=area_m2_expr / 1000000.0,  # type: ignore[arg-type]
                    period_start=period_start,
                    period_end=period_end,
                    cleaned=True,
                )
            )
            zones_count += 1
        await session.flush()

        stats_rows = (
            await session.execute(
                select(  # type: ignore[call-overload]
                    ChangeZone.change_class,
                    func.coalesce(func.sum(ChangeZone.area_km2), 0),
                )
                .where(
                    ChangeZone.result_territory_id == territory.id
                )
                .group_by(ChangeZone.change_class)
            )
        ).all()
        area_by_class: dict[str, Decimal] = {
            "retained": Decimal("0"),
            "lost": Decimal("0"),
            "new": Decimal("0"),
        }
        for change_class, area_value in stats_rows:
            key = str(change_class)
            if key in area_by_class:
                area_by_class[key] = Decimal(str(area_value))

        territory.retained_area_km2 = area_by_class["retained"]
        territory.lost_area_km2 = area_by_class["lost"]
        territory.new_area_km2 = area_by_class["new"]
        territory_total = sum(area_by_class.values(), Decimal("0"))
        aoi.area_km2 = territory_total
        total_area += territory_total
        await session.flush()

    result_row.territories_count = max(1, min(5, len(grouped)))
    result_row.total_area_km2 = {
        "total_km2": float(total_area),
        "note": "сумма по территориям (с возможным пересечением)",
        "zones": zones_count,
    }
    await session.flush()