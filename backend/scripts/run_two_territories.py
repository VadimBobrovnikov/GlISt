"""Два полных прогона: городской район и парк (после resolve)."""
from __future__ import annotations

import json
import time

import requests

BASE = "http://localhost:8000/api/v1"
QUERIES = [
    "Как изменилась зеленая зона в районе Восточный в Улан-Удэ за последний год?",
    "Как изменилась зеленая зона Парка Горького в Москве за последний год?",
]


def run_one(text: str) -> None:
    print("=== QUERY", text, flush=True)
    resolved = requests.post(
        f"{BASE}/territories/resolve",
        json={"text": text},
        timeout=120,
    ).json()
    print("RESOLVE", resolved.get("status"), flush=True)
    selected = resolved.get("selected") or {}
    geometry = selected.get("geometry")
    print(
        "SELECTED",
        selected.get("name"),
        "| kind",
        selected.get("kind"),
        "| type",
        selected.get("type"),
        "| parent",
        selected.get("parent"),
        "| city",
        selected.get("city"),
        "| geom",
        geometry.get("type") if isinstance(geometry, dict) else None,
        flush=True,
    )
    print("BBOX", selected.get("bbox"), flush=True)
    if resolved.get("status") != "resolved":
        print(json.dumps(resolved, ensure_ascii=True)[:1200], flush=True)
        return
    created = requests.post(
        f"{BASE}/queries",
        json={"text": text, "territory": selected},
        timeout=30,
    )
    print("CREATE", created.status_code, flush=True)
    created.raise_for_status()
    run_id = created.json()["run"]["id"]
    print("RUN_ID", run_id, flush=True)
    deadline = time.time() + 1200
    last = None
    status = ""
    while time.time() < deadline:
        run = requests.get(f"{BASE}/runs/{run_id}", timeout=20).json()
        status = str(run.get("status"))
        if status != last:
            print("STATUS", status, run.get("rejectionCategory"), flush=True)
            last = status
        if status in ("завершён", "не прошёл проверку", "ошибка", "данные недоступны"):
            break
        time.sleep(6)
    result = requests.get(f"{BASE}/runs/{run_id}/result", timeout=60)
    print("RESULT_CODE", result.status_code, flush=True)
    if result.status_code != 200:
        return
    data = result.json()
    diag = data.get("diagnostics") or {}
    print("OUTCOME", data.get("validationOutcome"), flush=True)
    print("AOI", json.dumps(diag.get("aoi"), ensure_ascii=True), flush=True)
    print("COVERAGE", json.dumps(diag.get("coverage")), flush=True)
    print("DATES", json.dumps(diag.get("actualDates")), flush=True)
    print("SEASONS", json.dumps(diag.get("seasons"), ensure_ascii=True), flush=True)
    print("AGG", json.dumps(data.get("aggregate")), flush=True)
    zones = requests.get(f"{BASE}/runs/{run_id}/zones", timeout=60)
    print(
        "ZONES",
        zones.status_code,
        len(zones.json().get("features") or []) if zones.ok else None,
        flush=True,
    )


def main() -> None:
    for query in QUERIES:
        run_one(query)


if __name__ == "__main__":
    main()
