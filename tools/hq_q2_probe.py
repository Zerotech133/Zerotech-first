#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ 控制面 Q2/SLO 探针（W3 前置，只读）。

覆盖：
- 8091 /health
- 9 个 /api/hq/* 直连端点
- 8734 /hq 页面
- 8734 /api/hq/snapshot 未登录 401（鉴权）
SLO：全部通过；API p95 < 800ms；页面 < 1500ms。
用法：python -X utf8 tools/hq_q2_probe.py [--json]
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_PATHS = [
    "/api/hq/snapshot", "/api/hq/org", "/api/hq/tasks", "/api/hq/tasks/board",
    "/api/hq/approvals", "/api/hq/approvals/summary", "/api/hq/budget",
    "/api/hq/budget/alerts", "/api/hq/heartbeat/status",
]


def _get(port: int, path: str, timeout: float = 5.0) -> tuple[int, bytes, float]:
    t0 = time.perf_counter()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        return r.status, body, (time.perf_counter() - t0) * 1000
    finally:
        c.close()


def build_probe() -> dict:
    # 冷启动预热：首拍会 import hq_* 模块/读注册表，不计入稳态 SLO。
    warm = None
    try:
        code, _, ms = _get(8091, "/api/hq/snapshot")
        warm = {"status": code, "ms": round(ms, 1)}
    except Exception as exc:  # noqa: BLE001
        warm = {"status": None, "ms": None, "error": str(exc)}
    checks = []
    def add(name, port, path, want):
        try:
            code, body, ms = _get(port, path)
            wants = want if isinstance(want, (tuple, list)) else (want,)
            ok = code in wants
            if ok and 200 in wants and path.startswith("/api/hq/"):
                try:
                    ok = json.loads(body.decode("utf-8")).get("ok") is True
                except Exception:  # noqa: BLE001
                    ok = False
            checks.append({"name": name, "port": port, "path": path, "want": want,
                           "got": code, "ms": round(ms, 1), "ok": ok})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "port": port, "path": path, "want": want,
                           "got": None, "ms": None, "ok": False, "error": str(exc)})
    add("health", 8091, "/health", 200)
    for p in API_PATHS:
        add("api " + p, 8091, p, 200)
    # /hq 页面壳已加 auth_request：未登录 302 登录或 401 均视为鉴权生效。
    add("page /hq auth gate", 8734, "/hq", [302, 401])
    add("unauth api 401", 8734, "/api/hq/snapshot", 401)
    api_lat = [c["ms"] for c in checks if c["name"].startswith("api ") and c["ms"] is not None]
    api_sorted = sorted(api_lat)
    p95 = round(api_sorted[max(0, math.ceil(0.95 * len(api_sorted)) - 1)], 1) if api_sorted else None
    p50 = round(api_sorted[len(api_sorted) // 2], 1) if api_sorted else None
    failures = [c for c in checks if not c["ok"]]
    # Q2/SLO 稳态门槛：9 端点 p95 < 800ms；页面 /hq < 1500ms（页面 SLO 必须真实参与判定）。
    p95_ok = p95 is not None and p95 < 800
    page_ms = [c["ms"] for c in checks if c["name"].startswith("page /hq") and c["ms"] is not None]
    page_ok = bool(page_ms) and page_ms[0] < 1500 and all(c["ok"] for c in checks if c["name"].startswith("page /hq"))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "schema": "hq/q2-probe/v1",
        "warmup": warm,
        "read_only": True,
        "checks": checks,
        "summary": {"total": len(checks), "passed": len(checks) - len(failures),
                    "failed": len(failures), "api_p50_ms": p50, "api_p95_ms": p95,
                    "p95_ok": p95_ok, "page_ms": page_ms[0] if page_ms else None, "page_ok": page_ok},
        "slo": {"availability": "99.5%", "api_p95_ms_lt": 800, "page_ms_lt": 1500, "auth_401_required": True},
        "ok": not failures and p95_ok and page_ok,
        "failures": failures,
    }


def render_md(p: dict) -> str:
    s = p["summary"]
    lines = ["# HQ 控制面 Q2/SLO 探针", "",
             f"- 生成：{p['generated_at']}｜检查 **{s['passed']}/{s['total']}**｜API p50 **{s['api_p50_ms']}ms** / p95 **{s['api_p95_ms']}ms**（<800ms: {s['p95_ok']}）｜页面 {s['page_ms']}ms（<1500ms: {s['page_ok']}）",
             "", "| 检查 | 端口 | 期望 | 实际 | ms | 结果 |", "|---|---:|---:|---:|---:|---|"]
    for c in p["checks"]:
        lines.append(f"| {c['name']} | {c['port']} | {c['want']} | {c.get('got')} | {c.get('ms')} | {'PASS' if c['ok'] else 'FAIL'} |")
    lines += ["", f"**结论：{'PASS' if p['ok'] else 'FAIL'}**"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ Q2/SLO 探针")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    p = build_probe()
    print(json.dumps(p, ensure_ascii=False, indent=1) if args.json else render_md(p), end="")
    return 0 if p["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
