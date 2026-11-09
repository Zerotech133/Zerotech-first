# -*- coding: utf-8 -*-
"""P1-01 retest: run the fixed portal_api in-process on a temp loopback port,
then exercise /api/flow/artifact as local, anonymous-remote, and token-remote."""
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

PORTAL = Path(r"C:\项目\老田的AI助手\.worktrees\K-SEC-20261108-FLOW-ARTIFACT-AUTH\portal")
os.environ["PORTAL_API_TOKEN"] = "p1-01-retest-token"
sys.path.insert(0, str(PORTAL))
import portal_api  # noqa: E402

PORT = 18092
srv = portal_api.http.server.ThreadingHTTPServer(("127.0.0.1", PORT), portal_api.Handler)
srv.daemon_threads = True
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
time.sleep(0.5)


def get(headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request("GET", "/api/flow/artifact?p=portal_api.py", headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, len(body), body[:200].decode("utf-8", "replace")


cases = {
    "local_loopback": {},
    "remote_anonymous": {"X-Portal-Proxy": "1", "X-Real-IP": "203.0.113.9"},
    "remote_with_token": {
        "X-Portal-Proxy": "1",
        "X-Real-IP": "203.0.113.9",
        "X-Portal-Token": "p1-01-retest-token",
    },
}
out = {"port": PORT, "cases": {}}
for name, headers in cases.items():
    try:
        status, size, preview = get(headers)
        out["cases"][name] = {"status": status, "bytes": size, "preview": preview}
    except Exception as exc:
        out["cases"][name] = {"error": repr(exc)}
srv.shutdown()
out["ok"] = out["cases"].get("remote_anonymous", {}).get("status") == 403
Path(r"C:\Temp\p1_01_retest_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
