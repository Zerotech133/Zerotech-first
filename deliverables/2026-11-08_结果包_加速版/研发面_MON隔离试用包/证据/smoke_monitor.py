#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Isolated, read-only smoke probe for /api/monitor/* (研发面试用包证据).

用法:
  python C:\Temp\smoke_monitor.py            # default 127.0.0.1:8091
  python C:\Temp\smoke_monitor.py http://127.0.0.1:8091
输出: 人类可读行 + JSON 结果文件 C:\Temp\monitor_smoke_<ts>.json
仅 GET；不调用任何写端点（POST /api/monitor/run 也不调用）。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
OUT_DIR = r"C:\Temp"


def probe(path):
    url = BASE + path
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            elapsed = round(time.time() - t0, 3)
            try:
                body = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                body = raw[:500].decode("utf-8", "replace")
            return {"path": path, "status": r.status, "elapsed_s": elapsed,
                    "json": isinstance(body, (dict, list)), "body": body}
    except urllib.error.HTTPError as ex:
        raw = ex.read()[:500]
        return {"path": path, "status": ex.code,
                "elapsed_s": round(time.time() - t0, 3),
                "body": raw.decode("utf-8", "replace")}
    except Exception as ex:  # noqa: BLE001
        return {"path": path, "status": None,
                "elapsed_s": round(time.time() - t0, 3),
                "error": "%s: %s" % (type(ex).__name__, ex)}


def main():
    probes = [
        "/health",
        "/api/monitor/pools",
        "/api/monitor/summary",
        "/api/monitor/alerts?pool=watchlist&limit=3",
    ]
    results = []
    for p in probes:
        r = probe(p)
        results.append(r)
        print("%-45s -> %-5s %.3fs json=%s" % (
            r["path"], r.get("status"), r.get("elapsed_s"), r.get("json")))
        if isinstance(r.get("body"), dict):
            print("    keys:", sorted(r["body"].keys())[:12])
        elif r.get("error"):
            print("    error:", r["error"])
        elif isinstance(r.get("body"), str):
            print("    body_head:", r["body"][:160].replace("\n", " "))
        if r.get("status") is None:
            # 服务不可达时仅记录，不中断后续（便于记录整体现状）
            continue

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUT_DIR, "monitor_smoke_%s.json" % ts)
    payload = {
        "probe": "smoke_monitor.py",
        "target": BASE,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "read-only GET probe; no POST/write endpoints called",
        "results": results,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("RESULT_FILE=%s" % out)

    ok = any(r.get("status") == 200 for r in results if r["path"] != "/health")
    if not ok:
        print("SMOKE RESULT: BLOCKED (no monitor endpoint returned 200)")
        return 3
    print("SMOKE RESULT: PASS (>=1 monitor endpoint 200)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
