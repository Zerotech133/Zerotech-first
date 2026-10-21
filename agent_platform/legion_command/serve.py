# -*- coding: utf-8 -*-
"""军团指挥界面套件 HTTP 服务（WO-LEGION-S22）。

端口 8796（独立于 8795 门面）。端点（全部只读 GET，除会终指令）：
  GET /api/snapshot        —— org/hr/tasks/schedules 聚合快照（沙盘数据源）
  GET /api/health          —— 服务与健康
  GET /                    —— 指挥界面（index.html，沙盘+会议室+工作台三视图）
  POST /api/dispatch       —— 会终指令：写军团门面 inbox（走 S21 投递协议）

纪律：快照端点只读；dispatch 是唯一写路径且只写 bridge_inbox inbox/
（田丰登记去重的既有机制），不直改 org/tasks/任何生产状态。
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402  S21 门面复用
from legion_command.snapshot import build_snapshot  # noqa: E402

_STATIC = Path(__file__).resolve().parent / "static"
_KIND_MAP = {"work": "task_request", "trade": "task_request", "study": "question"}


class Handler(BaseHTTPRequestHandler):
    server_version = "legion-command/1.0"

    def _send_json(self, obj, status=200):
        import json
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        if not path.exists():
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        import re
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file(_STATIC / "index.html", "text/html; charset=utf-8")
            return
        m = re.match(r"^/static/([a-z_]+\.html)$", path)
        if m and re.fullmatch(r"[a-z_]+", m.group(1).rsplit(".", 1)[0]):
            self._send_file(_STATIC / m.group(1), "text/html; charset=utf-8")
            return
        if path == "/api/snapshot":
            self._send_json(build_snapshot())
            return
        if path == "/api/health":
            self._send_json({"ok": True, "service": "legion-command", "version": "1.0"})
            return
        self._send_json({"ok": False, "error": "not_found: %s" % path}, 404)

    def do_POST(self):
        import json
        path = urlparse(self.path).path
        if path != "/api/dispatch":
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "error": "bad_request"}, 400)
            return
        otype = req.get("type", "work")
        kind = _KIND_MAP.get(otype)
        if kind is None:
            self._send_json({"ok": False, "error": "bad_type: %s" % otype}, 400)
            return
        payload = {
            "command": "legion_meeting_dispatch",
            "order_type": otype,
            "title": str(req.get("title", ""))[:120],
            "attendees": req.get("attendees", []),
            "host": "laotian",
            "note": str(req.get("note", ""))[:400],
        }
        env = inbox_gateway.submit(kind, payload, source="warroom")
        self._send_json(env)


def build_server(host="127.0.0.1", port=8796):
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="legion command console")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    srv = build_server(args.host, args.port)
    print("legion-command serving on %s:%d" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
