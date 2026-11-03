#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 Q2 健康探针（只读）：只 GET 本机只读端点，不写服务、不改 manifest。"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.request

TARGETS = {
    "acad1-legion": [
        ("pool", "http://127.0.0.1:8091/api/acad1/pool"),
        ("zhice-health", "http://127.0.0.1:8105/api/v1/health"),
    ],
    "data-warehouse": [
        ("dw-stats", "http://127.0.0.1:8091/api/dw/stats"),
        ("catalog", "http://127.0.0.1:8091/api/dw/search?q=__none__"),
    ],
}


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url, timeout=5.0):
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": 200 <= r.status < 300, "status": r.status,
                    "latency_ms": int((time.time() - t) * 1000),
                    "evidence": r.read(2048).decode("utf-8", "replace")[:500]}
    except Exception as e:
        return {"ok": False, "status": 0, "latency_ms": int((time.time() - t) * 1000),
                "error": type(e).__name__ + ":" + str(e)[:200]}


def probe(composition):
    checks = []
    for name, url in TARGETS.get(composition, []):
        r = _get(url)
        r.update({"name": name, "url": url})
        checks.append(r)
    return {"schema": "zerobound/composition_health_probe/v1",
            "composition": composition, "checked_at": _now(),
            "ok": bool(checks) and all(x["ok"] for x in checks), "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description="组合 Q2 健康探针（只读）")
    ap.add_argument("--composition", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    names = list(TARGETS) if a.all else ([a.composition] if a.composition else [])
    if not names or any(n not in TARGETS for n in names):
        print("q2_probe_error: 需要 --composition acad1-legion|data-warehouse 或 --all", file=sys.stderr)
        return 2
    rows = [probe(n) for n in names]
    out = {"schema": "zerobound/composition_health_probe_batch/v1",
           "generated_at": _now(), "results": rows,
           "summary": {"compositions": len(rows), "ok": sum(1 for r in rows if r["ok"])}}
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
          "\n".join("%s ok=%s checks=%s" % (r["composition"], r["ok"], [(c["name"], c["status"], c["ok"]) for c in r["checks"]]) for r in rows))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
