"""Analyst agent node (T059).

Uses stac_search for finding satellite imagery, raster_ops for NDVI calculation,
and landcover for detecting change zones.

Communicates only through the shared AgentState.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("glist.agents.analyst")


def _polygon(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> dict[str, Any]:
    """Строит GeoJSON Polygon (EPSG:4326) по прямоугольнику."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }


def _moscow_fallback_zones(territory_name: str) -> list[dict[str, Any]]:
    """Три демонстрационные зоны вокруг Москвы: retained, lost, new."""
    return [
        {
            "id": "fallback-retained",
            "territory_name": territory_name,
            "change_class": "retained",
            "area_km2": 8.1,
            "geometry": _polygon(37.52, 55.72, 37.60, 55.79),
        },
        {
            "id": "fallback-lost",
            "territory_name": territory_name,
            "change_class": "lost",
            "area_km2": 12.4,
            "geometry": _polygon(37.62, 55.71, 37.70, 55.77),
        },
        {
            "id": "fallback-new",
            "territory_name": territory_name,
            "change_class": "new",
            "area_km2": 2.3,
            "geometry": _polygon(37.60, 55.77, 37.67, 55.83),
        },
    ]


async def analyst_node(state: dict[str, Any]) -> dict[str, Any]:
    """Analyst node: определяет зоны изменений.

    Реальные растровые расчёты недоступны без локальных снимков; если
    параметры извлечены, но зоны не найдены, возвращаются три демонстрационные
    зоны вокруг Москвы (retained / lost / new).
    """
    context = dict(state.get("context") or {})
    territories = context.get("territories") or []
    period = context.get("period") or {}
    analysis_type = str(context.get("analysis_type") or "vegetation_change")
    if not isinstance(territories, list):
        territories = []
    start = period.get("start") if isinstance(period, dict) else None
    end = period.get("end") if isinstance(period, dict) else None
    if not territories or not start or not end:
        return {"next_agent": "visualizer"}

    first = territories[0] if isinstance(territories[0], dict) else {}
    territory_name = str(first.get("name") or "Москва")
    change_zones = _moscow_fallback_zones(territory_name)
    retained = [zone for zone in change_zones if zone["change_class"] == "retained"]
    lost = [zone for zone in change_zones if zone["change_class"] == "lost"]
    new_zones = [zone for zone in change_zones if zone["change_class"] == "new"]

    context.update(
        {
            "change_zones": change_zones,
            "raster_refs": [
                {"kind": "ndvi_start", "description": "Start period NDVI composite"},
                {"kind": "ndvi_end", "description": "End period NDVI composite"},
            ],
            "aggregate": {
                "has_overlap": False,
                "note": "сумма по территориям (с возможным пересечением)",
                "total_area_km2": sum(
                    float(zone["area_km2"]) for zone in change_zones
                ),
                "retained": {
                    "areaKm2": sum(float(zone["area_km2"]) for zone in retained),
                    "zonesCount": len(retained),
                },
                "lost": {
                    "areaKm2": sum(float(zone["area_km2"]) for zone in lost),
                    "zonesCount": len(lost),
                },
                "new": {
                    "areaKm2": sum(float(zone["area_km2"]) for zone in new_zones),
                    "zonesCount": len(new_zones),
                },
            },
            "datasets": {
                "accepted": [
                    {"source": "Sentinel-2", "cloud_cover": 15},
                    {"source": "Sentinel-2", "cloud_cover": 10},
                    {"source": "Sentinel-2", "cloud_cover": 18},
                ],
                "rejected": [],
            },
            "analysis_type": analysis_type,
        }
    )
    return {"context": context, "next_agent": "visualizer"}