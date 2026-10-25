# -*- coding: utf-8 -*-
"""移动端公网受限门 m_public_gate.py（2026-10-25，P0 公网方案执行）。

作用：给 cloudflared 等隧道提供一个**最小暴露面**的入口——只转发
  /m/                移动页
  /static/tokens.css 视觉 token
  /api/m/health|data|beacon  移动端接口
  /s/                分享短链（8091 校验后 302）
其余路径一律 403（绝不把 8080 的 98 条内部路由整体隧道出去）。

安全：
  · 不转发 Cookie / Authorization / X-Forwarded-* 到上游；
  · 上游固定 127.0.0.1:8080；
  · 简单按 IP 令牌桶限流（默认 120 次/分钟）；
  · 只允许 GET/POST。

用法：
  python m_public_gate.py --port 8081 --target http://127.0.0.1:8080
  python m_public_gate.py --port 8081 --dry-run   # 只打印白名单，不监听
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# 精确白名单：path 正则 → 是否允许 POST
RULES = [
    (re.compile(r"^/m(?:/.*)?$"), False),
    (re.compile(r"^/static/tokens\.css$"), False),
    (re.compile(r"^/api/m/health$"), False),
    (re.compile(r"^/api/m/data$"), False),
    (re.compile(r"^/api/m/beacon$"), True),
    (re.compile(r"^/api/m/auth/(start|check)$"), False),
    (re.compile(r"^/api/m/auth/exchange$"), True),
    (re.compile(r"^/api/m/ticket/(nudge|reply)$"), True),
    (re.compile(r"^/s/[A-Za-z0-9._-]+$"), False),
    (re.compile(r"^/favicon\.ico$"), False),
]
# 公网不开放的页面（即使有令牌也不给入口）；订阅页对外可看（空态/脱敏）
DENY_PATHS = re.compile(r"^/m/(ops)(?:\.html)?(?:/.*)?$")

RATE_LIMIT = 120          # 每 IP 每分钟
RATE_WINDOW = 60.0
STRIP_REQ_HEADERS = {"cookie", "authorization", "x-forwarded-for",
                     "x-forwarded-proto", "x-real-ip", "x-portal-token",
                     "x-portal-key", "x-push-hub-key"}


class _Limiter:
    def __init__(self, limit=RATE_LIMIT, window=RATE_WINDOW):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._hits = {}

    def allow(self, ip):
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(ip, [])
            q[:] = [t for t in q if now - t < self.window]
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


LIMITER = _Limiter()


def match_rule(path):
    if DENY_PATHS.match(path):
        return None
    for rx, allow_post in RULES:
        if rx.match(path):
            return allow_post
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "zb-m-gate/1.0"
    target = ("127.0.0.1", 8080)

    def log_message(self, fmt, *args):  # 降噪：只保留错误到 stderr
        if str(args[-1] if args else "")[:1] == "4":
            sys.stderr.write("[gate] %s %s\n" % (self.address_string(), fmt % args))

    def _deny(self, code, msg):
        body = json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _handle(self, method):
        ip = self.client_address[0] if self.client_address else "?"
        if not LIMITER.allow(ip):
            self._deny(429, "rate limited")
            return
        parts = urlsplit(self.path)
        path = parts.path or "/"
        allow_post = match_rule(path)
        if allow_post is None:
            self._deny(403, "forbidden path")
            return
        if method == "POST" and not allow_post:
            self._deny(405, "method not allowed")
            return
        if method not in ("GET", "POST"):
            self._deny(405, "method not allowed")
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        try:
            conn = http.client.HTTPConnection(self.target[0], self.target[1],
                                              timeout=30)
            hdrs = {}
            for k, v in self.headers.items():
                if k.lower() in STRIP_REQ_HEADERS:
                    continue
                hdrs[k] = v
            hdrs["Host"] = "%s:%d" % self.target
            hdrs["X-Forwarded-Host"] = self.headers.get("Host", "")
            conn.request(method, self.path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
        except Exception as e:  # noqa: BLE001
            self._deny(502, "upstream error: %s" % str(e)[:100])
            return
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in ("transfer-encoding", "connection", "keep-alive",
                      "content-length", "set-cookie"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-M-Gate", "1")
        self.end_headers()
        try:
            if method != "HEAD":
                self.wfile.write(data)
        except OSError:
            pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--target", default="http://127.0.0.1:8080")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    parts = urlsplit(args.target)
    Handler.target = (parts.hostname or "127.0.0.1", parts.port or 80)
    if args.dry_run:
        print("ALLOW:")
        for rx, post in RULES:
            print("  %-32s POST=%s" % (rx.pattern, post))
        print("DENY:", DENY_PATHS.pattern)
        return 0
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print("m_public_gate listening on %s:%d → %s (rate %d/min/IP)"
          % (args.bind, args.port, args.target, RATE_LIMIT), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
