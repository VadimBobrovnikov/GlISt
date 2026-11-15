"""Полный запуск по всему городу Улан-Удэ после resolve."""
from __future__ import annotations

import json
import time

import requests

BASE = "http://localhost:8000/api/v1"
TEXT = "Как изменилась зеленая зона Улан-Удэ за последний год?"


def main() -> None:
    resolved = requests.post(
        f"{BASE}/territories/resolve",
        json={"text": TEXT},
        timeout=60,
    ).json()
    print("RESOLVE", resolved.get("status"), flush=True)
    selected = resolved.get("selected") or {}
    print("SELECTED", selected.get("name"), selected.get("parent"), selected.get("type"))
    print("BBOX", selected.get("bbox"), flush=True)
    print(
        "GEOM",
        (selected.get("geometry") or {}).get("type")
        if isinstance(selected.get("geometry"), dict)
        else None,
        flush=True,
    )
    if resolved.get("status") != "resolved":
        print(json.dumps(resolved, ensure_ascii=True)[:1500], flush=True)
        return

    created = requests.post(
        f"{BASE}/queries",
        json={"text": TEXT, "territory": selected},
        timeout=30,
    )
    print("CREATE", created.status_code, flush=True)
    created.raise_for_status()
    run_id = created.json()["run"]["id"]
    print("RUN_ID", run_id, flush=True)

    deadline = time.time() + 1800
    last = None
    while time.time() < deadline:
        run = requests.get(f"{BASE}/runs/{run_id}", timeout=20).json()
        status = str(run.get("status"))
        if status != last:
            print("STATUS", status, run.get("rejectionCategory"), flush=True)
            last = status
        if status in ("завершён", "не прошёл проверку", "ошибка", "данные недоступны"):
            break
        time.sleep(8)

    result = requests.get(f"{BASE}/runs/{run_id}/result", timeout=60)
    print("RESULT_CODE", result.status_code, flush=True)
    if result.status_code != 200:
        print(result.text[:500], flush=True)
        return
    data = result.json()
    diag = data.get("diagnostics") or {}
    print("OUTCOME", data.get("validationOutcome"), flush=True)
    print("SUMMARY", data.get("summaryText"), flush=True)
    print("AOI", json.dumps(diag.get("aoi"), ensure_ascii=True), flush=True)
    print("COVERAGE", json.dumps(diag.get("coverage")), flush=True)
    print("DATES", json.dumps(diag.get("actualDates")), flush=True)
    print("SEASONS", json.dumps(diag.get("seasons"), ensure_ascii=True), flush=True)
    print("AGG", json.dumps(data.get("aggregate")), flush=True)
    print("LIMITS", json.dumps(data.get("qualityLimitations"), ensure_ascii=True)[:700], flush=True)


if __name__ == "__main__":
    main()
