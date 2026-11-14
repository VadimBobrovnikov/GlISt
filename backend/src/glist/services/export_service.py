"""Генерация реальных файлов экспорта PDF/GeoJSON (задачи T054/T056)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from matplotlib import font_manager
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import AnalysisResult, AnalysisRun, ChangeZone, ResultTerritory

CHANGE_CLASSES = ("retained", "lost", "new")
CLASS_LABELS = {
    "retained": "Сохранено (не изменение)",
    "lost": "Потеряно",
    "new": "Новые зоны",
}


@dataclass(frozen=True)
class ExportData:
    """Данные результата, необходимые для PDF и GeoJSON."""

    run_id: uuid.UUID
    result: AnalysisResult
    zones: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    @property
    def summary(self) -> str:
        return self.result.summary_text

    @property
    def explanation(self) -> str:
        return self.result.explanation_text


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


async def load_export_data(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> ExportData | None:
    """Загружает результат, зоны и диагностику для экспорта."""
    run = await session.get(AnalysisRun, run_id)
    if run is None:
        return None
    result_row = (
        await session.execute(
            select(AnalysisResult).where(AnalysisResult.run_id == run_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if result_row is None and run.reused_result_id is not None:
        result_row = await session.get(AnalysisResult, run.reused_result_id)
    if result_row is None:
        return None

    rows = (
        await session.execute(
            select(  # type: ignore[call-overload]
                ResultTerritory.territory_name,
                ChangeZone.change_class,
                ChangeZone.area_km2,
                ChangeZone.period_start,
                ChangeZone.period_end,
                func.ST_AsGeoJSON(ChangeZone.geom).label("geojson"),
            )
            .join(
                ResultTerritory,
                ResultTerritory.id == ChangeZone.result_territory_id,
            )
            .where(ResultTerritory.result_id == result_row.id)
        )
    ).all()

    zones: list[dict[str, Any]] = []
    for row in rows:
        geojson_text = row.geojson
        if not isinstance(geojson_text, str):
            continue
        try:
            geometry = json.loads(geojson_text)
        except json.JSONDecodeError:
            continue
        zones.append(
            {
                "territory_name": row.territory_name,
                "change_class": str(row.change_class),
                "area_km2": float(row.area_km2 or 0),
                "period_start": row.period_start,
                "period_end": row.period_end,
                "geometry": geometry,
            }
        )

    diagnostics = _as_dict(
        _as_dict(result_row.sources_summary).get("diagnostics")
    )
    return ExportData(
        run_id=run_id,
        result=result_row,
        zones=zones,
        diagnostics=diagnostics,
    )


def build_geojson(data: ExportData) -> dict[str, Any]:
    """Собирает FeatureCollection с атрибутами контракта."""
    features: list[dict[str, Any]] = []
    for zone in data.zones:
        features.append(
            {
                "type": "Feature",
                "geometry": zone["geometry"],
                "properties": {
                    "territory_name": zone["territory_name"],
                    "change_class": zone["change_class"],
                    "area_km2": round(float(zone["area_km2"]), 6),
                    "period_start": zone["period_start"].isoformat(),
                    "period_end": zone["period_end"].isoformat(),
                    "run_id": str(data.run_id),
                    "result_id": str(data.result.id),
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": features,
    }


def class_totals(data: ExportData) -> dict[str, dict[str, float]]:
    """Суммы площадей и количество полигонов по классам (PostGIS-значения)."""
    totals = {
        change_class: {"area_km2": 0.0, "zones": 0}
        for change_class in CHANGE_CLASSES
    }
    for zone in data.zones:
        change_class = str(zone["change_class"])
        if change_class in totals:
            totals[change_class]["area_km2"] += float(zone["area_km2"])
            totals[change_class]["zones"] += 1
    return totals


def format_area_km2(value: float) -> str:
    """Не округляет малые ненулевые площади до 0.0."""
    if value == 0:
        return "0"
    if abs(value) < 0.01:
        return f"{value * 1_000_000:.0f} м²"
    if abs(value) < 1:
        return f"{value:.4f} км²"
    return f"{value:.2f} км²"


def net_change_percent(data: ExportData) -> float | None:
    totals = class_totals(data)
    total = sum(item["area_km2"] for item in totals.values())
    if total <= 0:
        return None
    return (totals["new"]["area_km2"] - totals["lost"]["area_km2"]) / total * 100


def _register_font() -> str:
    """Регистрирует DejaVu Sans (кириллица) для PDF; иначе Helvetica."""
    try:
        font_path = font_manager.findfont("DejaVu Sans")
        pdfmetrics.registerFont(TTFont("GlIStSans", str(font_path)))
        return "GlIStSans"
    except Exception:  # noqa: BLE001 — fallback на встроенный шрифт
        return "Helvetica"


def build_pdf(data: ExportData) -> bytes:
    """Формирует PDF: сводка, таблица классов, покрытие, объяснение, источники."""
    font = _register_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "GlIStTitle",
        parent=styles["Title"],
        fontName=font,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
    )
    header_style = ParagraphStyle(
        "GlIStHeader",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=4,
        textColor=colors.HexColor("#0F172A"),
    )
    body_style = ParagraphStyle(
        "GlIStBody",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#0F172A"),
    )

    totals = class_totals(data)
    coverage = _as_dict(data.diagnostics.get("coverage"))
    limitations = data.diagnostics.get("limitations")
    if not isinstance(limitations, list):
        limitations = []

    story: list[Any] = [
        Paragraph("GlISt — результат анализа", title_style),
        Paragraph(f"Запуск: {data.run_id}", body_style),
        Spacer(1, 6),
        Paragraph("Сводка", header_style),
        Paragraph(data.summary or "Сводка отсутствует.", body_style),
    ]

    net = net_change_percent(data)
    net_text = "не определён (нет площади)" if net is None else f"{net:+.2f}%"
    story.append(
        Paragraph(
            "Процент изменения = (новые − потерянные) / сумма площадей × 100% = "
            f"{net_text}.",
            body_style,
        )
    )
    classification = _as_dict(data.diagnostics.get("classification"))
    story.append(
        Paragraph(
            "NDVI = (B08 − B04) / (B08 + B04); "
            f"порог классификации = {classification.get('ndviThreshold', '—')}; "
            f"мин. площадь = {classification.get('minAreaM2', '—')} м².",
            body_style,
        )
    )

    table_rows: list[list[str]] = [["Класс", "Площадь", "Полигонов"]]
    for change_class in CHANGE_CLASSES:
        item = totals[change_class]
        table_rows.append(
            [
                CLASS_LABELS[change_class],
                format_area_km2(float(item["area_km2"])),
                str(int(item["zones"])),
            ]
        )
    table = Table(table_rows, colWidths=[80 * mm, 45 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.extend([Spacer(1, 6), table, Spacer(1, 6)])

    story.append(Paragraph("Качество данных", header_style))
    story.append(
        Paragraph(
            "Валидное покрытие: "
            f"начало {float(coverage.get('validStart') or 0) * 100:.1f}%, "
            f"конец {float(coverage.get('validEnd') or 0) * 100:.1f}%, "
            "общее "
            f"{float(coverage.get('sharedValid') or 0) * 100:.1f}%.",
            body_style,
        )
    )
    seasons = _as_dict(data.diagnostics.get("seasons"))
    story.append(
        Paragraph(
            "Сезоны: "
            f"{seasons.get('start') or '—'} и {seasons.get('end') or '—'}; "
            f"сопоставимы: {'да' if seasons.get('comparable') else 'нет'}.",
            body_style,
        )
    )
    if limitations:
        story.append(
            Paragraph(
                "Ограничения: " + "; ".join(str(item) for item in limitations),
                body_style,
            )
        )
    else:
        story.append(
            Paragraph("Существенных ограничений не зафиксировано.", body_style)
        )

    story.append(Paragraph("Объяснение", header_style))
    story.append(
        Paragraph(data.explanation or "Объяснение отсутствует.", body_style)
    )

    story.append(Paragraph("Источники данных", header_style))
    source_rows: list[list[str]] = [["Период", "Сцена", "Дата", "Облачность, %"]]
    scenes = _as_dict(data.diagnostics.get("scenes"))
    for role, label in (("start", "До"), ("end", "После")):
        items = scenes.get(role)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            cloud = item.get("cloudCover")
            source_rows.append(
                [
                    label,
                    str(item.get("sceneId") or ""),
                    str(item.get("datetime") or "")[:10],
                    f"{float(cloud):.1f}"
                    if isinstance(cloud, (int, float))
                    else "—",
                ]
            )
    if len(source_rows) == 1:
        story.append(
            Paragraph("Источники не сохранены для этого запуска.", body_style)
        )
    else:
        source_table = Table(
            source_rows,
            colWidths=[22 * mm, 80 * mm, 28 * mm, 30 * mm],
        )
        source_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ]
            )
        )
        story.append(source_table)

    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "Площади рассчитаны в PostGIS по геометриям зон; "
            "сумма по территориям может включать пересечения.",
            body_style,
        )
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    document.build(story)
    return buffer.getvalue()


def write_export_file(
    data: ExportData,
    fmt: str,
    artifacts_dir: str | Path,
) -> Path:
    """Пишет PDF/GeoJSON в каталог артефактов и возвращает путь."""
    run_dir = Path(artifacts_dir) / str(data.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "pdf":
        path = run_dir / f"analysis_{data.run_id}.pdf"
        path.write_bytes(build_pdf(data))
        return path
    path = run_dir / f"analysis_{data.run_id}.geojson"
    path.write_text(
        json.dumps(build_geojson(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def artifact_to_dict(artifact: Any) -> dict[str, Any]:
    """Преобразует ORM Artifact в схему ответа API."""
    return {
        "id": artifact.id,
        "runId": artifact.run_id,
        "kind": artifact.kind,
        "sizeBytes": artifact.size_bytes,
        "sha256": artifact.checksum,
        "isHeavy": artifact.is_heavy,
        "retentionUntil": artifact.retention_until,
        "purgeNoticeAt": None,
        "isDeleted": False,
        "contentUrl": f"/api/v1/artifacts/{artifact.id}/content",
        "createdAt": artifact.created_at,
    }