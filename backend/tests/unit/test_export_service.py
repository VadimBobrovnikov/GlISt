"""Проверки реальной генерации PDF/GeoJSON."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from glist.services.export_service import (
    ExportData,
    build_geojson,
    build_pdf,
    format_area_km2,
    write_export_file,
)


def _data() -> ExportData:
    run_id = uuid.uuid4()
    result: Any = SimpleNamespace(
        id=uuid.uuid4(),
        summary_text="Потеряно зон: 1, новые зоны: 0.",
        explanation_text="Тестовое объяснение.",
        sources_summary={},
    )
    zones: list[dict[str, Any]] = [
        {
            "territory_name": "Москва",
            "change_class": "lost",
            "area_km2": 0.0022,
            "period_start": date(2020, 6, 1),
            "period_end": date(2023, 6, 1),
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [37.5, 55.7],
                        [37.6, 55.7],
                        [37.6, 55.8],
                        [37.5, 55.8],
                        [37.5, 55.7],
                    ]
                ],
            },
        }
    ]
    diagnostics: dict[str, Any] = {
        "coverage": {"validStart": 0.9, "validEnd": 0.8, "sharedValid": 0.7},
        "classification": {"ndviThreshold": 0.25, "minAreaM2": 1000},
        "seasons": {"start": "лето", "end": "лето", "comparable": True},
        "limitations": ["Тестовое ограничение"],
        "scenes": {
            "start": [
                {
                    "sceneId": "S2-START",
                    "datetime": "2020-06-01T00:00:00Z",
                    "cloudCover": 5,
                }
            ],
            "end": [
                {
                    "sceneId": "S2-END",
                    "datetime": "2023-06-01T00:00:00Z",
                    "cloudCover": 8,
                }
            ],
        },
    }
    return ExportData(
        run_id=run_id,
        result=result,
        zones=zones,
        diagnostics=diagnostics,
    )


def test_build_geojson_contains_zone_properties() -> None:
    payload = build_geojson(_data())

    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 1
    properties = payload["features"][0]["properties"]
    assert properties["territory_name"] == "Москва"
    assert properties["change_class"] == "lost"
    assert properties["area_km2"] == 0.0022
    assert properties["period_start"] == "2020-06-01"
    assert properties["period_end"] == "2023-06-01"


def test_build_pdf_returns_real_pdf_bytes() -> None:
    pdf = build_pdf(_data())
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_write_export_file_creates_both_formats(tmp_path: Path) -> None:
    data = _data()

    geojson_path = write_export_file(data, "geojson", tmp_path)
    pdf_path = write_export_file(data, "pdf", tmp_path)

    assert geojson_path.suffix == ".geojson"
    assert pdf_path.suffix == ".pdf"
    assert geojson_path.is_file()
    assert pdf_path.is_file()
    json.loads(geojson_path.read_text(encoding="utf-8"))


def test_export_data_reports_scenario_title_and_labels() -> None:
    """PDF/GeoJSON обязаны называть сценарий и его классы (регрессия @property)."""
    base = _data()
    flood = ExportData(
        run_id=base.run_id,
        result=base.result,
        zones=base.zones,
        diagnostics=base.diagnostics,
        analysis_type="flood_extent",
    )

    assert flood.scenario_title == "Зоны затопления"
    assert flood.scenario_labels["new"] == "Временное затопление"
    assert build_pdf(flood).startswith(b"%PDF")
    assert build_geojson(flood)["features"][0]["properties"]["change_class"] == "lost"


def test_format_area_does_not_round_small_nonzero_to_zero() -> None:
    assert format_area_km2(0.0022) == "2200 м²"
    assert format_area_km2(0.0004) == "400 м²"
    assert format_area_km2(0.5) == "0.5000 км²"
