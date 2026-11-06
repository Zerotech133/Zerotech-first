# -*- coding: utf-8 -*-
"""判决性实验：urlopen 对回环地址是否被代理接管。

三段对照，同一目标（本机临时 HTTP 服务），只改代理环境：
  C1 无代理             → 应成功（基线）
  C2 HTTP_PROXY=<死端口> → 若回环被豁免也该成功；实际失败 = 被判给代理
  C3 HTTP_PROXY=<死端口> + no_proxy=127.0.0.1 → 应成功（证明标准豁免开关有效）
  C4 HTTP_PROXY=<死端口> + ProxyHandler({})  → 应成功（证明空 handler 旁路有效）

用法：python proof_loopback_proxy.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "from": "loopback"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


CHILD = r'''
import json, sys, urllib.request
mode, url = sys.argv[1], sys.argv[2]
try:
    if mode == "no_proxy_handler":
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        r = op.open(url, timeout=3)
    else:
        r = urllib.request.urlopen(url, timeout=3)
    body = r.read().decode("utf-8", "replace")
    print(json.dumps({"ok": True, "code": r.status, "body": body}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"ok": False, "err": type(e).__name__, "detail": str(e)[:200]}, ensure_ascii=False))
'''


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/health" % port
    dp = dead_port()

    base_env = {k: v for k, v in os.environ.items()
                if k.lower() not in ("http_proxy", "https_proxy", "no_proxy", "all_proxy")}

    cases = [
        ("C1 无代理（基线）", "plain", dict(base_env)),
        ("C2 死代理 %d，无豁免" % dp, "plain",
         dict(base_env, HTTP_PROXY="http://127.0.0.1:%d" % dp, http_proxy="http://127.0.0.1:%d" % dp)),
        ("C3 死代理 + no_proxy=127.0.0.1", "plain",
         dict(base_env, HTTP_PROXY="http://127.0.0.1:%d" % dp, http_proxy="http://127.0.0.1:%d" % dp,
              NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")),
        ("C4 死代理 + ProxyHandler({})", "no_proxy_handler",
         dict(base_env, HTTP_PROXY="http://127.0.0.1:%d" % dp, http_proxy="http://127.0.0.1:%d" % dp)),
    ]

    results = []
    for label, mode, env in cases:
        p = subprocess.run([PY, "-X", "utf8", "-c", CHILD, mode, url],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        raw = (p.stdout or "").strip().splitlines()
        try:
            out = json.loads(raw[-1]) if raw else {"ok": False, "err": "no_output"}
        except Exception:
            out = {"ok": False, "err": "bad_output", "detail": (p.stdout or p.stderr or "")[:200]}
        results.append({"case": label, "target": url, "result": out})
        print("%-34s ok=%-5s %s" % (label, out.get("ok"), out.get("err") or out.get("body", "")[:50]))

    srv.shutdown()

    # 顺带记录真实代理环境下 urllib 对回环的判定
    probe = r'''
import json, os, urllib.request
out = {"env_http_proxy": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
       "proxy_bypass_127": urllib.request.proxy_bypass("127.0.0.1"),
       "proxy_bypass_8787": urllib.request.proxy_bypass("127.0.0.1:8787"),
       "getproxies": urllib.request.getproxies()}
print(json.dumps(out, ensure_ascii=False))
'''
    p = subprocess.run([PY, "-X", "utf8", "-c", probe], capture_output=True, text=True, encoding="utf-8")
    aux = json.loads((p.stdout or "{}").strip().splitlines()[-1]) if p.stdout.strip() else {}
    print("\n[环境事实] %s" % json.dumps(aux, ensure_ascii=False))

    # 证据落系统临时目录，别污染仓库（脚本会被反复重跑）
    outdir = Path(tempfile.gettempdir()) / "loopback_proof"
    outdir.mkdir(parents=True, exist_ok=True)
    ev = outdir / "proof_loopback_proxy.json"
    ev.write_text(
        json.dumps({"cases": results, "env_facts": aux}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[证据] %s" % ev)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
