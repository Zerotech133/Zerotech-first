#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 SLO 告警闭环 v1（只读评估）。

读取本机只读端点，按组合阈值输出 ok/alert；不改 manifest、不写 active。
  python -X utf8 tools/q2_slo_alert.py --all --json
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.request

ENDPOINTS = {
    "acad1-legion": "http://127.0.0.1:8091/api/acad1/pool",
    "data-warehouse": "http://127.0.0.1:8091/api/dw/stats",
}


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"_error": type(e).__name__ + ":" + str(e)[:160]}


def evaluate(composition: str) -> dict:
    data = _get(ENDPOINTS.get(composition, ""))
    alerts, checks = [], []
    if data.get("_error"):
        return {"composition": composition, "ok": False, "checked_at": _now(),
                "alerts": ["probe_failed:" + data["_error"]], "checks": []}
    if composition == "acad1-legion":
        checks = [
            ("freshness_days<=1", data.get("freshness_days"), lambda v: isinstance(v, (int, float)) and v <= 1),
            ("stale=false", data.get("stale"), lambda v: v is False),
            ("pool_total<=30", (data.get("summary") or {}).get("pool_total"), lambda v: isinstance(v, (int, float)) and v <= 30),
            ("data_date_present", data.get("data_date"), lambda v: bool(v)),
        ]
    elif composition == "data-warehouse":
        total = data.get("total") or 0
        reg = data.get("registered") or 0
        exp = data.get("exposed") or 0
        reg_ratio = (reg / total) if total else None
        exp_ratio = (exp / total) if total else None
        checks = [
            ("registered_ratio>=0.95", reg_ratio, lambda v: isinstance(v, (int, float)) and v >= 0.95),
            ("exposed_ratio>=0.80", exp_ratio, lambda v: isinstance(v, (int, float)) and v >= 0.80),
            ("state=ok", data.get("state"), lambda v: v == "ok"),
        ]
    for name, value, fn in checks:
        ok = bool(fn(value))
        if not ok:
            alerts.append("%s (actual=%r)" % (name, value))
    return {"composition": composition, "ok": not alerts, "checked_at": _now(),
            "alerts": alerts,
            "checks": [{"name": n, "value": v, "ok": bool(fn(v))} for n, v, fn in checks]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合 SLO 告警评估（只读）")
    ap.add_argument("--composition", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    names = list(ENDPOINTS) if a.all else ([a.composition] if a.composition else [])
    if not names or any(n not in ENDPOINTS for n in names):
        print("slo_error: 需要 --composition 或 --all", file=sys.stderr)
        return 2
    results = [evaluate(n) for n in names]
    summary = {"compositions": len(results), "ok": sum(1 for r in results if r["ok"]),
               "alerts": sum(len(r["alerts"]) for r in results)}
    out = {"schema": "zerobound/composition_slo_alert/v1", "generated_at": _now(),
           "results": results, "summary": summary}
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
          "\n".join("%s ok=%s alerts=%s" % (r["composition"], r["ok"], r["alerts"]) for r in results))
    return 0 if summary["alerts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
