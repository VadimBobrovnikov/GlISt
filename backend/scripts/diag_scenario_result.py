"""Сводка по результату сценария (для проверки в отчёте)."""

from __future__ import annotations

import json
import sys

import requests

BASE = "http://localhost:8000/api/v1"


def main(run_id: str) -> None:
    run = requests.get(f"{BASE}/runs/{run_id}", timeout=60).json()
    print(
        "run:",
        json.dumps(
            {
                "status": run.get("status"),
                "analysisType": run.get("analysisType"),
                "analysisTypeLabel": run.get("analysisTypeLabel"),
                "category": run.get("rejectionCategory"),
                "reason": run.get("statusReason"),
            },
            ensure_ascii=False,
        ),
    )
    result = requests.get(f"{BASE}/runs/{run_id}/result", timeout=120).json()
    diag = result.get("diagnostics") or {}
    print("outcome:", result.get("validationOutcome"), result.get("summaryText"))
    print("classLabels:", json.dumps(result.get("classLabels"), ensure_ascii=False))
    print("coverage:", json.dumps(diag.get("coverage"), ensure_ascii=False))
    print("dates:", json.dumps(diag.get("dates"), ensure_ascii=False))
    print("period:", json.dumps(diag.get("period"), ensure_ascii=False))
    print("periodSelection:", json.dumps(diag.get("periodSelection"), ensure_ascii=False))
    print("scaling:", json.dumps(diag.get("scaling"), ensure_ascii=False))
    print("grid:", json.dumps(diag.get("grid"), ensure_ascii=False))
    print("aggregate:", json.dumps(result.get("aggregate"), ensure_ascii=False))
    print("territories:", len(result.get("territories") or []))
    print("sources:", len(result.get("sources") or []))
    print("limitations:")
    for item in result.get("qualityLimitations") or []:
        print("  -", item)
    zones = requests.get(f"{BASE}/runs/{run_id}/zones", timeout=600)
    print("zones:", zones.status_code, len(zones.text or ""))


if __name__ == "__main__":
    main(sys.argv[1])
