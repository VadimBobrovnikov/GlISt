"""Статическая раздача build/web на 8081 без кеширования (для ручной проверки)."""
from __future__ import annotations

import functools
import http.server
from pathlib import Path

WEB_DIR = Path(r"D:\glist\frontend\build\web")
PORT = 8081


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


def main() -> None:
    handler = functools.partial(NoCacheHandler, directory=str(WEB_DIR))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"Serving {WEB_DIR} on http://localhost:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
