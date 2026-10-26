# -*- coding: utf-8 -*-
"""军团 Agent IDE · 独立形态服务（legion_ide）。

定位：把原本寄生在 8734 门户里的 Agent IDE（`portal/ide.html` + 预烘
`portal/agent_ide_bundle.json`）独立成**自己的进程/端口/启动器/App 窗口**，
不依赖 portal 页面路由与 nginx；数据仍用同一份预烘 bundle（单一真源不动）。

接口：
  GET  /                       → portal/ide.html（只读施工台）
  GET  /agent_ide_bundle.json  → 预烘 bundle（no-store）
  GET  /api/health             → 服务/资源/门户代理可达性
  POST /ollama/api/chat        → 转发门户代理（不可达时 503 JSON，页面可降级）

纪律：
  · 只绑 127.0.0.1；路径白名单，无目录穿越；POST 体上限 1MB；
  · 不写任何真源、不落凭据；日志只打一行启动横幅（-v 才逐请求）；
  · 端口 8103 已入册 `tools/check_ports.py` REGISTRY（同一工单提交）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8103
DEFAULT_PORTAL = "http://127.0.0.1:8091"
REPO = Path(__file__).resolve().parents[2]
MAX_BODY = 1024 * 1024

_JSON_CT = "application/json; charset=utf-8"
_HTML_CT = "text/html; charset=utf-8"


def _resource(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "bytes": 0, "mtime": None}
    return {"exists": True, "bytes": st.st_size,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))}


def build_health(repo: Path, portal_url: str, port: int) -> dict:
    repo = Path(repo)
    ide = repo / "portal" / "ide.html"
    bundle = repo / "portal" / "agent_ide_bundle.json"
    return {
        "ok": True,
        "service": "legion-ide",
        "version": VERSION,
        "pid": os.getpid(),
        "bind": "%s:%s" % (DEFAULT_HOST, port),
        "ide_html": _resource(ide),
        "bundle": _resource(bundle),
        "portal_proxy": {"url": portal_url, "reachable": _portal_alive(portal_url)},
        "note": "只读施工台；数据=预烘 bundle；聊天走门户代理（可降级）",
    }


def _portal_alive(url: str, timeout: float = 0.6) -> bool:
    """门户代理可达性：有任意 HTTP 响应即算可达（不要求特定路径存在）。"""
    base = url.rstrip("/")
    for path in ("/api/health", "/"):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as r:
                return 200 <= r.status < 500
        except urllib.error.HTTPError:
            return True          # 有 HTTP 错误响应 = 服务在，只是没这个路径
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def make_handler(repo: Path, portal_url: str):
    """构造 handler 类（repo/portal 注入，便于测试用 tmp 仓）。"""
    repo = Path(repo)

    class Handler(BaseHTTPRequestHandler):
        server_version = "LegionIDE/" + VERSION

        def log_message(self, fmt, *args):  # 静默；-v 才在 serve 层打
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       _JSON_CT)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/ide.html"):
                ide = repo / "portal" / "ide.html"
                if not ide.is_file():
                    return self._json(503, {"ok": False, "error": "ide_html_missing",
                                            "path": str(ide)})
                return self._send(200, ide.read_bytes(), _HTML_CT)
            if path == "/agent_ide_bundle.json":
                bundle = repo / "portal" / "agent_ide_bundle.json"
                if not bundle.is_file():
                    return self._json(503, {"ok": False, "error": "bundle_missing",
                                            "path": str(bundle)})
                return self._send(200, bundle.read_bytes(), _JSON_CT)
            if path == "/api/health":
                return self._json(200, build_health(repo, portal_url, self.server.server_port))
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            return self._json(404, {"ok": False, "error": "not_found", "path": path})

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/ollama/api/chat":
                return self._json(404, {"ok": False, "error": "not_found", "path": path})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"ok": False, "error": "bad_content_length",
                                        "limit": MAX_BODY})
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type") or _JSON_CT
            req = urllib.request.Request(
                portal_url.rstrip("/") + "/ollama/api/chat", data=body,
                headers={"Content-Type": ctype}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = r.read()
                    return self._send(r.status, data, _JSON_CT)
            except urllib.error.HTTPError as exc:
                return self._send(exc.code, exc.read(), _JSON_CT)
            except (urllib.error.URLError, OSError) as exc:
                return self._json(503, {
                    "ok": False, "error": "portal_proxy_unavailable",
                    "portal": portal_url,
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                })

    return Handler


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                  repo: Path | None = None,
                  portal_url: str = DEFAULT_PORTAL) -> ThreadingHTTPServer:
    handler = make_handler(Path(repo) if repo else REPO, portal_url)
    httpd = ThreadingHTTPServer((host, int(port)), handler)
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="军团 Agent IDE · 独立形态服务")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--portal", default=DEFAULT_PORTAL, help="聊天代理目标（portal_api）")
    ap.add_argument("--health", action="store_true", help="只打印健康 JSON 后退出")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.health:
        print(json.dumps(build_health(Path(args.repo), args.portal, args.port),
                         ensure_ascii=False))
        return 0

    httpd = create_server(args.host, args.port, repo=Path(args.repo),
                          portal_url=args.portal)
    url = "http://%s:%s/" % (args.host, httpd.server_port)
    print("[legion-ide] listening %s  repo=%s  portal=%s"
          % (url, args.repo, args.portal), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
