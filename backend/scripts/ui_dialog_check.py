"""Проверка диалога подтверждения территории в реальном браузере (CDP).

Открывает актуальный frontend, запускает предпросмотр территории (без
спутникового расчёта) и снимает диалог на нескольких размерах окна.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import requests
import websockets

BASE = "http://localhost:8081/"
TMP = os.environ.get("TEMP", ".")
QUERY = "Как изменилась зеленая зона в Казани за последний год?"


async def main() -> None:
    targets = requests.get("http://127.0.0.1:9222/json/list", timeout=5).json()
    page = next(t for t in targets if t.get("type") == "page")
    ws = await websockets.connect(
        page["webSocketDebuggerUrl"], max_size=None, open_timeout=10
    )
    counter = 0

    async def cmd(method: str, params: dict | None = None) -> dict:
        nonlocal counter
        counter += 1
        mid = counter
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if message.get("id") == mid:
                return message

    async def size(width: int, height: int) -> None:
        await cmd(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await asyncio.sleep(2)

    async def click(x: int, y: int) -> None:
        for event_type in ("mousePressed", "mouseReleased"):
            await cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
        await asyncio.sleep(0.5)

    async def shot(name: str) -> None:
        result = await cmd("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result["result"]["data"])
        path = os.path.join(TMP, name)
        with open(path, "wb") as handle:
            handle.write(data)
        print("SHOT", path, len(data), flush=True)

    await cmd("Page.enable")
    await size(1440, 900)
    await cmd("Page.navigate", {"url": "about:blank"})
    await asyncio.sleep(1)
    await cmd("Page.navigate", {"url": BASE})
    await asyncio.sleep(10)

    # Ввод запроса: клик по полю, затем вставка текста (без расчёта).
    await click(720, 350)
    await cmd("Input.insertText", {"text": QUERY})
    await asyncio.sleep(1)
    await click(720, 489)
    await asyncio.sleep(45)
    await shot("glist_dlg_1440.png")

    await size(1024, 700)
    await shot("glist_dlg_1024.png")

    await size(800, 560)
    await shot("glist_dlg_800.png")

    await size(900, 420)
    await shot("glist_dlg_short.png")

    await size(430, 760)
    await shot("glist_dlg_narrow.png")

    # Раскрытые технические сведения: координаты не должны обрезаться.
    await size(1440, 900)
    await click(400, 679)
    await shot("glist_dlg_details.png")

    await ws.close()


asyncio.run(main())
