"""Проверка запроса района Восточный в Улан-Удэ: resolve + анализ."""
from __future__ import annotations

import json
import time

import requests

BASE = "http://localhost:8000/api/v1"
TEXT = (
    "Как изменилась зеленая зона Улан-Удэ в районе Восточный "
    "за последний год?"
)


def main() -> None:
    resolved = requests.post(
        f"{BASE}/territories/resolve",
        json={"text": TEXT},
        timeout=60,
    )
    print("RESOLVE_STATUS", resolved.status_code, flush=True)
    payload = resolved.json()
    print("RESOLVE", json.dumps(payload, ensure_ascii=True)[:2500], flush=True)
    if payload.get("status") != "resolved":
        return
    selected = payload["selected"]
    print("SELECTED_NAME", selected.get("name"), "PARENT", selected.get("parent"))
    print("SELECTED_BBOX", selected.get("bbox"))
    print(
        "SELECTED_GEOM",
        (selected.get("geometry") or {}).get("type")
        if isinstance(selected.get("geometry"), dict)
        else None,
    )

    created = requests.post(
        f"{BASE}/queries",
        json={"text": TEXT, "territory": selected},
        timeout=30,
    )
    print("CREATE", created.status_code, flush=True)
    created.raise_for_status()
    run_id = created.json()["run"]["id"]
    print("RUN_ID", run_id, flush=True)

    deadline = time.time() + 900
    status = ""
    while time.time() < deadline:
        run = requests.get(f"{BASE}/runs/{run_id}", timeout=20).json()
        status = str(run.get("status"))
        if status in ("завершён", "не прошёл проверку", "ошибка", "данные недоступны"):
            print("STATUS", status, run.get("rejectionCategory"), run.get("statusReason"), flush=True)
            break
        time.sleep(6)
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
    print(
        "TERRITORY_SELECTION",
        json.dumps(diag.get("territorySelection"), ensure_ascii=True)[:1000],
        flush=True,
    )
    print("COVERAGE", json.dumps(diag.get("coverage")), flush=True)
    print("DATES", json.dumps(diag.get("actualDates")), flush=True)
    print("SEASONS", json.dumps(diag.get("seasons"), ensure_ascii=True), flush=True)
    print("LIMITS", json.dumps(data.get("qualityLimitations"), ensure_ascii=True)[:800], flush=True)


if __name__ == "__main__":
    main()
