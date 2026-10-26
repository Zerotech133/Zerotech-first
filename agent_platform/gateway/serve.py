# -*- coding: utf-8 -*-
"""军团门面 HTTP 服务（WO-LEGION-S21）。

给外部板块（飞书中台）的回环 HTTP 端点（127.0.0.1:8795）：
  POST /llm/chat        {binding?, system, user}      → llm_gateway.chat 信封
  POST /inbox/submit    {kind, payload, source?}      → inbox_gateway.submit 信封
  GET  /inbox/receipt/{id}                            → inbox_gateway.receipt 信封
  GET  /health                                        → {ok:true, service:"legion-gateway"}

实现纪律：标准库 http.server，零新依赖；信封 JSON UTF-8；错误也走信封
（HTTP 状态码只区分 200 成功/400 参数坏/404 路由错，业务失败在信封 ok 字段）。

用法（手动/测试）：
  python agent_platform/gateway/serve.py            # 前台 8795
  python agent_platform/gateway/serve.py --port 8796 --check
挂载为常驻服务须走五要素授权（本单只交付代码与用法）。

OSLINK-W4 通道②（K-QUANT-20261026-OSLEG-8795API，默认关闭）：
  仅当 env OSLEG_HTTP_API=1 时启用 POST /osleg/v1/{submit,consume,answer,deliver}
  与 GET /osleg/v1/receipt（端点层见 gateway/osleg_http_api.py；token/store 均需
  显式注入，未配置即 503 失败关闭）。未开启时这些路径行为与既有版本完全一致。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, llm_gateway  # noqa: E402
from gateway.envelope import make_envelope  # noqa: E402

# OSLINK-W4 通道② 开关（K-QUANT-20261026-OSLEG-8795API）：
# 仅当 env OSLEG_HTTP_API 显式开启时把 /osleg/v1/* 路由到 osleg_http_api；
# 默认关闭 = 现役行为一字不变（未开启时这些路径仍走原 404）。
_OSLEG_TRUTHY = ("1", "true", "yes", "on")
_OSLEG_PREFIX = "/osleg/v1"


def _osleg_enabled():
    return os.environ.get("OSLEG_HTTP_API", "").strip().lower() in _OSLEG_TRUTHY


def _osleg_dispatch(method, path, query, body, headers):
    """懒加载通道②端点层（默认关闭时不 import、不改任何既有行为）。"""
    from gateway import osleg_http_api
    return osleg_http_api.handle(method=method, path=path, headers=headers,
                                 body=body, query=query)


def _osleg_query(raw_query):
    query = {}
    for key, values in parse_qs(raw_query or "").items():
        if values:
            query[key] = values[-1]
    return query


class Handler(BaseHTTPRequestHandler):
    server_version = "legion-gateway/1.0"

    # ---- 基础 ----
    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bad_request(self, message):
        self._send(make_envelope(kind="http", ok=False,
                                 error={"code": "bad_request", "message": message}), 400)

    def log_message(self, fmt, *args):  # 静默默认访问日志（测试输出干净）
        pass

    # ---- 路由 ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send({"ok": True, "service": "legion-gateway", "version": "1.0"})
            return
        if path.startswith("/inbox/receipt/"):
            msg_id = path[len("/inbox/receipt/"):].strip("/")
            self._send(inbox_gateway.receipt(msg_id))
            return
        if _osleg_enabled() and path.startswith(_OSLEG_PREFIX + "/"):
            status, payload = _osleg_dispatch(
                "GET", path, _osleg_query(urlparse(self.path).query), None,
                {key: value for key, value in self.headers.items()})
            self._send(payload, status)
            return
        self._send(make_envelope(kind="http", ok=False,
                                 error={"code": "not_found", "message": path}), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._bad_request("body must be UTF-8 JSON")
            return
        if _osleg_enabled() and path.startswith(_OSLEG_PREFIX + "/"):
            status, payload = _osleg_dispatch(
                "POST", path, {}, req,
                {key: value for key, value in self.headers.items()})
            self._send(payload, status)
            return
        if path == "/llm/chat":
            if not isinstance(req, dict) or not isinstance(req.get("user", ""), str):
                self._bad_request("user must be string")
                return
            self._send(llm_gateway.chat(binding=req.get("binding"),
                                        system=req.get("system", ""),
                                        user=req.get("user", "")))
            return
        if path == "/inbox/submit":
            self._send(inbox_gateway.submit(req.get("kind", ""),
                                            req.get("payload") or {},
                                            source=req.get("source", "external")))
            return
        self._send(make_envelope(kind="http", ok=False,
                                 error={"code": "not_found", "message": path}), 404)


def build_server(host="127.0.0.1", port=8795):
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="legion gateway serve")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--check", action="store_true",
                    help="不自检外部依赖，仅启动即探活后退出（冒烟用）")
    args = ap.parse_args(argv)
    srv = build_server(args.host, args.port)
    if args.check:
        print("legion-gateway up on %s:%d (check mode)" % (args.host, args.port))
        srv.server_close()
        return 0
    print("legion-gateway serving on %s:%d" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
