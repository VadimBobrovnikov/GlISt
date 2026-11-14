"""Повтор пользовательского запроса Казани через API и ожидание результата."""
from __future__ import annotations

import json
import time

import requests

BASE = "http://localhost:8000/api/v1"
TEXT = "как изменилась зеленая зона в Казани за последний год?"


def main() -> None:
    created = requests.post(f"{BASE}/queries", json={"text": TEXT}, timeout=30)
    print("CREATE", created.status_code, created.text[:300], flush=True)
    created.raise_for_status()
    run_id = created.json()["run"]["id"]
    print("RUN_ID", run_id, flush=True)

    deadline = time.time() + 480
    last_status: str | None = None
    status = ""
    while time.time() < deadline:
        run = requests.get(f"{BASE}/runs/{run_id}", timeout=20).json()
        status = str(run.get("status"))
        if status != last_status:
            print(
                "STATUS",
                status,
                run.get("rejectionCategory"),
                run.get("statusReason"),
                flush=True,
            )
            last_status = status
        if status in ("завершён", "не прошёл проверку", "ошибка", "данные недоступны"):
            break
        time.sleep(6)

    result = requests.get(f"{BASE}/runs/{run_id}/result", timeout=60)
    print("RESULT_CODE", result.status_code, flush=True)
    if result.status_code != 200:
        print(result.text[:300], flush=True)
        return
    data = result.json()
    diagnostics = data.get("diagnostics") or {}
    print("OUTCOME", data.get("validationOutcome"), flush=True)
    print("SUMMARY", data.get("summaryText"), flush=True)
    print("FAILURE", json.dumps(diagnostics.get("failure"), ensure_ascii=True), flush=True)
    print(
        "PERIOD",
        json.dumps(diagnostics.get("periodSelection"), ensure_ascii=True)[:1500],
        flush=True,
    )
    print("DATES", json.dumps(diagnostics.get("actualDates")), flush=True)
    print(
        "SEASONS",
        json.dumps(diagnostics.get("seasons"), ensure_ascii=True),
        flush=True,
    )
    print("COVERAGE", json.dumps(diagnostics.get("coverage")), flush=True)
    print(
        "INTERP",
        json.dumps(diagnostics.get("interpretability"), ensure_ascii=True),
        flush=True,
    )
    print(
        "LIMITS",
        json.dumps(data.get("qualityLimitations"), ensure_ascii=True)[:1000],
        flush=True,
    )
    print("AGG", json.dumps(data.get("aggregate")), flush=True)
    print("TERRITORIES", len(data.get("territories") or []), flush=True)


if __name__ == "__main__":
    main()
