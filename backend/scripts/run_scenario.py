"""Запуск сценария через API и ожидание результата (для контрольных прогонов)."""

from __future__ import annotations

import json
import sys
import time

import requests

BASE = "http://localhost:8000/api/v1"


def main(query_text: str, analysis_type: str, territory_text: str) -> None:
    resolved = requests.post(
        BASE + "/territories/resolve",
        json={"text": territory_text},
        timeout=240,
    ).json()
    selected = resolved.get("selected") or {}
    print(
        "resolve:",
        resolved.get("status"),
        bool(selected.get("geometry")),
        selected.get("bbox"),
        flush=True,
    )
    territory = {
        "name": selected.get("name"),
        "displayName": selected.get("displayName"),
        "type": selected.get("type"),
        "parent": selected.get("parent"),
        "bbox": selected.get("bbox"),
        "geometry": selected.get("geometry"),
    }
    created = requests.post(
        BASE + "/queries",
        json={
            "text": query_text,
            "analysisType": analysis_type,
            "territory": territory,
        },
        timeout=120,
    )
    print("create:", created.status_code, flush=True)
    if created.status_code >= 400:
        print(created.text[:500], flush=True)
        return
    run_id = created.json()["run"]["id"]
    print("RUN", run_id, flush=True)
    started = time.time()
    while time.time() - started < 2400:
        time.sleep(15)
        data = requests.get(BASE + f"/runs/{run_id}", timeout=90).json()
        print(int(time.time() - started), "s", data.get("status"), flush=True)
        if data.get("status") in ("завершён", "не прошёл проверку", "ошибка"):
            print("REASON", data.get("statusReason"), flush=True)
            break


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
