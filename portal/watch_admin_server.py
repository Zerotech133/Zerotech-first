# -*- coding: utf-8 -*-
"""Read-only admin HTTP server for the Watch multi-Agent management & model
comparison UI (M2 real read-only + M3 A/B shadow comparison).

Hard constraints (must hold):
  * Independent process bound ONLY to 127.0.0.1:8796. Never touches 8091 and
    never imports or modifies portal_api.py.
  * Exactly two routes:
        GET /watch-admin                -> serve watch_admin_page.html
        GET /api/watch-admin/snapshot  -> WatchAdminAggregator().build() (JSON, 15s cache)
    Everything else -> 404.
  * Read-only. No business state is written by this process. The shadow zone
    inside the snapshot is locked (rehearsal valve) and never connects to a real
    broker or Feishu.

Run:  python portal/watch_admin_server.py
Stop: Ctrl-C (or send it a SIGTERM). It does NOT stay resident in the portal.
"""

from __future__ import annotations
from typing import Any

import json
import importlib
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from watch_admin_snapshot import (  # noqa: E402
    SCHEMA,
    WatchAdminAggregator,
    sanitize_inf_nan,
)


HOST = "127.0.0.1"
# 2026-10-30 WO-WATCH-ADMIN：8796 已注册给军团指挥台，watch_admin 换 8789。
PORT = int(os.environ.get("WATCH_ADMIN_PORT", "8789"))
PAGE_FILE = _HERE / "watch_admin_page.html"
# Parallel-built, isolated feature modules (each owns its own file; no shared edits).
# They expose API_ROUTES (path -> callable(query)->dict) and/or HTML_ROUTES (path -> str).
_FEATURE_MODULES = [
    "watch_admin_route_chanlun",
    "watch_admin_route_shadow",
    "watch_admin_route_backtest",
    "watch_admin_route_combos",
    "watch_admin_route_contract",
    "watch_admin_route_alert",
    "watch_admin_route_ackdash",
]


class _Handler(BaseHTTPRequestHandler):
    """Minimal read-only request handler: two routes, 404 otherwise."""

    # ---- helpers -------------------------------------------------------- #
    def _send(
        self, code: int, body: Any, ctype: str = "application/json; charset=utf-8"
    ) -> None:
        if isinstance(body, (dict, list)):
            # Sanitize non-finite floats (NaN/Inf from source files like
            # last_backtest.json) so the payload is strictly valid JSON.
            body = json.dumps(sanitize_inf_nan(body), ensure_ascii=False).encode(
                "utf-8"
            )
        elif isinstance(body, str):
            body = body.encode("utf-8")
        else:
            body = bytes(body)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, code: int, text: str) -> None:
        self._send(code, text, "text/html; charset=utf-8")

    # ---- routes --------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/watch-admin", "/watch-admin/"):
            if PAGE_FILE.exists():
                self._send_html(200, PAGE_FILE.read_text(encoding="utf-8"))
            else:
                self._send(404, {"error": "page_not_found", "path": str(PAGE_FILE)})
            return

        # 2026-10-30 WO-WATCHADMIN-STATIC：托管 /static/（tokens/nav/ui）
        if path.startswith("/static/"):
            rel = unquote(path[len("/static/"):])
            base = (_HERE / "static").resolve()
            target = (base / rel).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                self._send(403, {"error": "forbidden"})
                return
            if target.is_file():
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), ctype)
            else:
                self._send(404, {"error": "not_found", "path": rel})
            return

        if path == "/api/watch-admin/snapshot":
            replay = query.get("replay_at", [None])[0]
            try:
                data = WatchAdminAggregator().build(replay_at=replay)
                self._send(200, data)
            except Exception as exc:  # noqa: BLE001
                self._send(
                    200,
                    {
                        "schema": SCHEMA,
                        "error": "build_failed",
                        "detail": str(exc)[:300],
                        "warnings": ["build_failed:%s" % type(exc).__name__],
                    },
                )
            return

        # ---- dynamic feature routes (parallel-built, isolated modules) ----
        for _mod in _FEATURE_MODULES:
            try:
                _m = importlib.import_module(_mod)
            except ImportError:
                continue
            _api = getattr(_m, "API_ROUTES", {})
            if path in _api:
                try:
                    _data = _api[path](query)
                    self._send(200, _data)
                except Exception as _exc:  # noqa: BLE001
                    self._send(
                        200,
                        {
                            "error": "feature_failed",
                            "module": _mod,
                            "detail": str(_exc)[:300],
                        },
                    )
                return
            _html = getattr(_m, "HTML_ROUTES", {})
            if path in _html:
                _target = _html[path]
                if isinstance(_target, str):
                    _p = Path(_target)
                    if _p.exists():
                        self._send_html(200, _p.read_text(encoding="utf-8"))
                    else:
                        self._send_html(200, _target)
                return
        # ---- end dynamic feature routes ----

        self._send(
            404,
            {
                "error": "not_found",
                "path": path,
                "routes": [
                    "/watch-admin",
                    "/api/watch-admin/snapshot",
                    "/watch-admin/chanlun",
                    "/api/watch-admin/chanlun",
                    "/watch-admin/shadow",
                    "/api/watch-admin/shadow",
                    "/watch-admin/backtest",
                    "/api/watch-admin/backtest",
                ],
            },
        )

    # Silence the default request logging to keep the console clean.
    def log_message(self, *args: Any) -> None:  # noqa: D401
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    print(
        "[watch-admin] read-only M2/M3 server on http://%s:%d (independent port; 8091 untouched)"
        % (HOST, PORT)
    )
    print(
        "[watch-admin] routes: GET /watch-admin · GET /api/watch-admin/snapshot · feature routes (chanlun/shadow/backtest) · others 404"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[watch-admin] shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
