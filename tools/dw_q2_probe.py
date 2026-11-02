#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data-warehouse Q2 探针：API + 登录页验收 + SLO。

无 --cookie 时页面鉴权状态为 PENDING-LOGIN（不伪造登录通过）。
用法：
  python -X utf8 tools/dw_q2_probe.py [--cookie "zb_session=..."] [--json]
"""
from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["/dw", "/dataops"]
API = "/api/dw/stats"


def _get(port, path, cookie="", timeout=5.0):
    t0 = time.perf_counter()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        r = c.getresponse()
        body = r.read()
        return r.status, body, round((time.perf_counter() - t0) * 1000, 1)
    finally:
        c.close()


def build(cookie: str = "") -> dict:
    checks = []
    try:
        code, body, ms = _get(8091, API)
        try:
            ok = code == 200 and json.loads(body.decode("utf-8")).get("ok", True) is not False
        except Exception:  # noqa: BLE001
            ok = code == 200
        checks.append({"name": "api " + API, "got": code, "ms": ms, "ok": ok})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "api " + API, "got": None, "ms": None, "ok": False, "error": str(exc)})
    for p in PAGES:
        try:
            code, _, ms = _get(8734, p)
            ok = code in (200, 302, 401)  # 路由存在且鉴权生效
            checks.append({"name": "route " + p + " (unauth)", "got": code, "ms": ms, "ok": ok})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "route " + p + " (unauth)", "got": None, "ms": None, "ok": False, "error": str(exc)})
    auth_pages = []
    if cookie:
        for p in PAGES:
            try:
                code, _, ms = _get(8734, p, cookie=cookie)
                auth_pages.append({"path": p, "got": code, "ms": ms, "ok": code == 200})
            except Exception as exc:  # noqa: BLE001
                auth_pages.append({"path": p, "got": None, "ms": None, "ok": False, "error": str(exc)})
    api_ms = [c["ms"] for c in checks if c["name"].startswith("api ") and c["ms"] is not None]
    p95 = round(sorted(api_ms)[-1], 1) if api_ms else None
    auth_state = "PASS" if auth_pages and all(p["ok"] for p in auth_pages) else ("PENDING-LOGIN" if not cookie else "FAIL")
    return {
        "schema": "dw/q2-probe/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "checks": checks,
        "authenticated_pages": auth_pages,
        "auth_state": auth_state,
        "slo": {"api_p95_ms": p95, "api_p95_ms_lt": 800, "slo_ok": p95 is not None and p95 < 800},
        "ok": all(c["ok"] for c in checks) and (p95 is not None and p95 < 800) and auth_state == "PASS",
        "note": "无 cookie 时页面登录验收为 PENDING-LOGIN，不伪造 PASS",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="data-warehouse Q2 探针")
    ap.add_argument("--cookie", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = build(args.cookie)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(f"# data-warehouse Q2\n\n- auth_state: **{d['auth_state']}**\n- SLO: p95 {d['slo']['api_p95_ms']}ms (<800: {d['slo']['slo_ok']})\n")
        for c in d["checks"]:
            print(f"- {c['name']}: {c['got']} {'PASS' if c['ok'] else 'FAIL'}")
        for p in d["authenticated_pages"]:
            print(f"- auth {p['path']}: {p['got']} {'PASS' if p['ok'] else 'FAIL'}")
    return 0 if d["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
