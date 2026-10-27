# -*- coding: utf-8 -*-
"""②③端点级真 HTTP 冒烟（临时端口 8092，不触碰 8091 生产进程）。

生产 8091 被旧进程占住（S4U 杀不动 + K234 幂等锁 skip），本脚本用
同款 mixin 继承链在 8092 起最小 Handler，验证 do_GET → mixin →
数据层全链与 URL 参数解析。验证完自杀。
"""

import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(r"C:/项目/老田的AI助手")
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "agent_platform"))

import api_approvals  # noqa: E402
import api_replay  # noqa: E402
import api_unified  # noqa: E402


class MinimalHandler(
    api_unified.Handler,
    api_replay.Handler,
    api_approvals.Handler,
    BaseHTTPRequestHandler,
):
    """最小宿主：补 _json、mini do_GET（复刻 portal_api 的两个分支 dispatch）。"""

    def do_GET(self):
        raw = self.path
        path = raw.split("?", 1)[0]
        qs = raw.split("?", 1)[1] if "?" in raw else ""
        if path == "/api/unified/projection":
            self._unified_projection()
        elif path == "/api/platform/replay":
            self._platform_replay(qs)
        elif path == "/api/platform/approvals":
            self._platform_approvals(qs)
        else:
            self._json({"error": "not_found"}, 404)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 8092), MinimalHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ok = True
    try:
        # ② 统一投影
        u = json.loads(
            opener.open("http://127.0.0.1:8092/api/unified/projection", timeout=30)
            .read()
            .decode()
        )
        print(
            "[② unified] domains:",
            u.get("summary", {}).get("domains_wired"),
            "| approvals:",
            u.get("summary", {}).get("approval_total"),
            "| findings:",
            u.get("findings"),
        )
        print(
            "  gzh verdict:",
            u.get("gzh", {}).get("verdict"),
            "| events:",
            u.get("events", {}).get("record_count"),
        )
        # ③ 回放：全量
        r = json.loads(
            opener.open("http://127.0.0.1:8092/api/platform/replay?limit=5", timeout=30)
            .read()
            .decode()
        )
        print(
            "[③ replay] matched:",
            r.get("matched_total"),
            "| returned:",
            r.get("returned"),
            "| canonical_valid:",
            r.get("canonical_valid"),
        )
        print("  timeline top:", dict(list(r.get("timeline", {}).items())[:3]))
        e = (r.get("events") or [{}])[0]
        print(
            "  newest:",
            e.get("ts"),
            e.get("kind"),
            "hash:",
            (e.get("event_hash") or "")[:12],
        )
        # ③ 过滤
        r2 = json.loads(
            opener.open(
                "http://127.0.0.1:8092/api/platform/replay?kind=gzh.approve&limit=3",
                timeout=30,
            )
            .read()
            .decode()
        )
        print("[③ filter kind=gzh.approve] matched:", r2.get("matched_total"))
        # 注册表
        a = json.loads(
            opener.open(
                "http://127.0.0.1:8092/api/platform/approvals?limit=5", timeout=30
            )
            .read()
            .decode()
        )
        print(
            "[注册表] total:",
            a.get("total"),
            "| by_domain:",
            a.get("by_domain"),
            "| errors:",
            a.get("errors"),
        )
        rec = (a.get("records") or [{}])[0]
        print(
            "  newest:",
            rec.get("decided_at"),
            rec.get("target_id"),
            f"{rec.get('from_status')}->{rec.get('to_status')}",
            rec.get("decision"),
        )
        # ③ 非法参数 → 400
        try:
            opener.open(
                "http://127.0.0.1:8092/api/platform/replay?since=not-a-date", timeout=15
            )
            print("[③ invalid since] UNEXPECTED 200")
            ok = False
        except urllib.error.HTTPError as ex:
            print("[③ invalid since] HTTP", ex.code, ex.read(100).decode())
            ok = ok and ex.code == 400
        # 注册表非法参数 → 400
        try:
            opener.open(
                "http://127.0.0.1:8092/api/platform/approvals?limit=abc", timeout=15
            )
            print("[注册表 invalid limit] UNEXPECTED 200")
            ok = False
        except urllib.error.HTTPError as ex:
            print("[注册表 invalid limit] HTTP", ex.code)
            ok = ok and ex.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
    print("SMOKE", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
