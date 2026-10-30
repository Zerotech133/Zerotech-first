#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""trace 运营化 v0：读取 Phoenix span，做延迟/错误/调用量汇总与告警。

只读 Phoenix REST；不新增常驻服务、不改 8795/8091。
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("PHOENIX_BASE", "http://127.0.0.1:6006").rstrip("/")
OUTDIR = ROOT / "work" / "p0" / "trace_ops"
DEFAULT_PROJECT = os.environ.get("PHOENIX_PROJECT", "default")


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _get_json(url: str, timeout: float = 8.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def fetch_spans(project: str = DEFAULT_PROJECT, limit: int = 100,
                base: str = BASE) -> dict:
    url = "%s/v1/projects/%s/spans?limit=%d" % (base.rstrip("/"), project, max(1, int(limit)))
    status, data = _get_json(url)
    return {"ok": status == 200 and isinstance(data, dict), "status": status,
            "spans": (data or {}).get("data") or []}


def _latency_ms(span: dict) -> float | None:
    try:
        start = datetime.fromisoformat(str(span.get("start_time")))
        end = datetime.fromisoformat(str(span.get("end_time")))
        return max(0.0, (end - start).total_seconds() * 1000.0)
    except Exception:
        return None


def summarize(spans: list, match: str = "llm") -> dict:
    if match:
        spans = [s for s in spans if match in str(s.get("name") or "")]
    lat = [x for x in (_latency_ms(s) for s in spans) if x is not None]
    names = Counter(str(s.get("name") or "?") for s in spans)
    errors = 0
    for s in spans:
        attrs = s.get("attributes") or {}
        if s.get("status_code") == "ERROR":
            errors += 1
        elif isinstance(attrs, dict) and str(attrs.get("llm.ok")).lower() == "false":
            errors += 1
    lat_sorted = sorted(lat)

    def pct(p):
        if not lat_sorted:
            return None
        idx = min(len(lat_sorted) - 1, int(round((p / 100.0) * (len(lat_sorted) - 1))))
        return round(lat_sorted[idx], 1)

    return {
        "count": len(spans),
        "errors": errors,
        "error_rate": round(errors / len(spans), 4) if spans else None,
        "latency_ms": {"p50": pct(50), "p95": pct(95), "max": round(max(lat), 1) if lat else None},
        "spans": dict(names.most_common(10)),
    }


def check(summary: dict, max_error_rate: float = 0.05) -> dict:
    alerts = []
    if summary["count"] == 0:
        alerts.append({"severity": "high", "code": "no_trace_data",
                       "message": "Phoenix 没有 span：检查 PHOENIX_TRACE/OTLP 是否开启"})
    if summary.get("error_rate") is not None and summary["error_rate"] > max_error_rate:
        alerts.append({"severity": "high", "code": "error_rate_high",
                       "value": summary["error_rate"], "message": "span 错误率过高"})
    return {"ok": not alerts, "alerts": alerts}


def status(project: str = DEFAULT_PROJECT, limit: int = 100,
           match: str = "llm") -> dict:
    fetched = fetch_spans(project, limit)
    summary = summarize(fetched["spans"], match=match)
    summary["phoenix_ok"] = fetched["ok"]
    summary["phoenix_status"] = fetched["status"]
    summary["project"] = project
    summary["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    summary.update(check(summary))
    return summary


def report(data: dict) -> dict:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    jp = OUTDIR / ("trace_%s.json" % stamp)
    mp = OUTDIR / ("trace_%s.md" % stamp)
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Phoenix trace 日汇总 %s" % stamp, "",
          "- project：%s" % data.get("project"),
          "- span 数：%s" % data.get("count"),
          "- 错误：%s（%.2f%%）" % (data.get("errors"), (data.get("error_rate") or 0) * 100),
          "- P50/P95/max：%s / %s / %s ms" % (
              (data.get("latency_ms") or {}).get("p50"),
              (data.get("latency_ms") or {}).get("p95"),
              (data.get("latency_ms") or {}).get("max")),
          "- spans：%s" % json.dumps(data.get("spans") or {}, ensure_ascii=False),
          "", "告警：%s" % (json.dumps(data.get("alerts") or [], ensure_ascii=False))]
    mp.write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix trace 运营汇总")
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--match", default="llm", help="span 名称过滤；空字符串=全部")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    data = status(args.project, args.limit, args.match)
    if args.report:
        data["report"] = report(data)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.check and not data.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
