"""Проверка перехода из истории: клик по третьей строке и снимок (CDP)."""
from __future__ import annotations

import asyncio
import base64
import json
import os

import requests
import websockets


async def main() -> None:
    targets = requests.get("http://127.0.0.1:9222/json/list", timeout=5).json()
    page = next(t for t in targets if t.get("type") == "page")
    ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
    counter = 0

    async def cmd(method: str, params: dict | None = None) -> dict:
        nonlocal counter
        counter += 1
        mid = counter
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
            if message.get("id") == mid:
                return message

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
        await asyncio.sleep(0.4)

    await cmd("Page.enable")
    await cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
    )
    await cmd("Page.navigate", {"url": "about:blank"})
    await asyncio.sleep(1)
    await cmd("Page.navigate", {"url": "http://localhost:8081/"})
    await asyncio.sleep(9)
    await click(1050, 50)
    await asyncio.sleep(4)
    await click(700, 285)
    await asyncio.sleep(10)
    result = await cmd("Page.captureScreenshot", {"format": "png"})
    data = base64.b64decode(result["result"]["data"])
    path = os.path.join(os.environ.get("TEMP", "."), "glist_c_history_open.png")
    with open(path, "wb") as handle:
        handle.write(data)
    print("SHOT", path, len(data), flush=True)
    await ws.close()


asyncio.run(main())
