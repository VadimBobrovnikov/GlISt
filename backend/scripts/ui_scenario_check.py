"""Браузерная проверка сценариев (CDP): карточка → диалог → результат.

Запускает Edge с remote debugging отдельно; скрипт подключается к первой
вкладке, снимает экраны и печатает текущий hash маршрута Flutter.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

import requests
import websockets

BASE = "http://localhost:8081/"
TMP = os.environ.get("TEMP", ".")


async def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "home"
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
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
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
        await asyncio.sleep(1.5)

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
        await asyncio.sleep(0.6)

    async def select_all() -> None:
        for event_type in ("keyDown", "keyUp"):
            await cmd(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "modifiers": 2,
                    "key": "a",
                    "code": "KeyA",
                    "windowsVirtualKeyCode": 65,
                    "nativeVirtualKeyCode": 65,
                },
            )
        await asyncio.sleep(0.3)

    async def shot(name: str) -> None:
        result = await cmd("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result["result"]["data"])
        path = os.path.join(TMP, name)
        with open(path, "wb") as handle:
            handle.write(data)
        print("SHOT", path, len(data), flush=True)

    async def hash_value() -> str:
        result = await cmd(
            "Runtime.evaluate",
            {"expression": "location.hash", "returnByValue": True},
        )
        return str(result.get("result", {}).get("result", {}).get("value"))

    await cmd("Page.enable")
    await size(1440, 900)

    if phase == "home":
        await cmd("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(1)
        await cmd("Page.navigate", {"url": BASE})
        await asyncio.sleep(12)
        await shot("glist_s_home.png")
    elif phase == "flood":
        query = "Покажи зоны затопления"  # без даты: должен появиться выбор даты
        await click(720, 350)
        await cmd("Input.insertText", {"text": query})
        await asyncio.sleep(1)
        await shot("glist_s_typed.png")
    elif phase == "retype":
        # Заменяет текст в поле запроса (Ctrl+A → вставка) и снимает экран.
        query = sys.argv[2]
        await click(720, 350)
        await select_all()
        await cmd("Input.insertText", {"text": query})
        await asyncio.sleep(1)
        await shot(sys.argv[3])
    elif phase == "typeat":
        # Клик по координатам, Ctrl+A, вставка текста.
        await click(int(sys.argv[2]), int(sys.argv[3]))
        await select_all()
        await cmd("Input.insertText", {"text": sys.argv[4]})
        await asyncio.sleep(1)
        await shot(sys.argv[5])
    elif phase == "submit":
        # Нажимает «Запустить анализ» и снимает то, что появилось.
        await click(720, 521)
        await asyncio.sleep(float(sys.argv[2]))
        await shot(sys.argv[3])
    elif phase == "shot":
        await shot(sys.argv[2])
    elif phase == "click":
        await click(int(sys.argv[2]), int(sys.argv[3]))
        await asyncio.sleep(2)
        await shot(sys.argv[4])
    elif phase == "nav":
        # Сброс на about:blank обязателен: смена только hash не перезагружает
        # документ Flutter, и deep link остался бы без эффекта.
        await cmd("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(1.5)
        await cmd("Page.navigate", {"url": sys.argv[2]})
        await asyncio.sleep(12)
        await shot(sys.argv[3])
        print("HASH", await hash_value(), flush=True)
    elif phase == "reload":
        await cmd("Page.reload", {"ignoreCache": True})
        await asyncio.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 12)
        await shot(sys.argv[3])
        print("HASH", await hash_value(), flush=True)
    elif phase == "hash":
        print("HASH", await hash_value(), flush=True)
    elif phase == "wait_shot":
        await asyncio.sleep(float(sys.argv[2]))
        await shot(sys.argv[3])
        print("HASH", await hash_value(), flush=True)

    await ws.close()


asyncio.run(main())
