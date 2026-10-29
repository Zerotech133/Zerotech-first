# -*- coding: utf-8 -*-
"""作战屏 v1.1 · 影子/回测只读聚合（LV-03，2026-10-29）。

只读 `data/shadow/shadow_ledger.jsonl` + `data/academy/backtests.jsonl`：
影子最近日期/条数/状态分布/降级率/latency p50；回测条数/状态/Go-No-Go 计数。
**不读取/不透出** symbol、name、price_at_decision、target_range、owner；分桶 n<5 显示 "<5"。
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS = 5000
K = 5


def _mask(n):
    n = int(n or 0)
    if 0 < n < K:
        return "<5"
    return n


def _read_rows(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(d, dict):
                    rows.append(d)
    except OSError:
        return []
    return rows[-MAX_ROWS:]


def build_risk_real(root=None):
    root = Path(root or ROOT)
    shadow = _read_rows(root / "agent_platform" / "data" / "shadow"
                        / "shadow_ledger.jsonl")
    tests = _read_rows(root / "agent_platform" / "data" / "academy"
                       / "backtests.jsonl")
    if not shadow and not tests:
        return None
    out = {}
    as_of = ""

    def _date_of(r):
        d = r.get("date")
        if isinstance(d, str) and d:
            return d
        ts = r.get("ts")
        if isinstance(ts, (int, float)):
            try:
                return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
            except (ValueError, OSError, OverflowError):
                return ""
        return ""

    if shadow:
        last_date = max(_date_of(r) for r in shadow)
        status = {}
        for r in shadow:
            s = str(r.get("status") or "unknown")
            status[s] = status.get(s, 0) + 1
        degraded = sum(1 for r in shadow if r.get("degraded") is True)
        lat = [float(r.get("latency_s")) for r in shadow
               if isinstance(r.get("latency_s"), (int, float))]
        out["shadow"] = {
            "last_date": last_date or None,
            "rows": len(shadow),
            "status": {k: _mask(v) for k, v in status.items()},
            "degraded_rate": round(degraded / len(shadow), 4),
            "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        }
        as_of = max(as_of, str(last_date or "")[:10])
    if tests:
        status = {}
        gng = {}
        for r in tests:
            status[str(r.get("status") or "unknown")] = (
                status.get(str(r.get("status") or "unknown"), 0) + 1)
            gng[str(r.get("go_no_go") or "unknown")] = (
                gng.get(str(r.get("go_no_go") or "unknown"), 0) + 1)
        last_created = max(str(r.get("created") or "") for r in tests)
        out["backtests"] = {
            "total": len(tests),
            "status": {k: _mask(v) for k, v in status.items()},
            "go_no_go": {k: _mask(v) for k, v in gng.items()},
            "last_created": last_created or None,
        }
        as_of = max(as_of, str(last_created or "")[:10])
    return {"as_of": as_of or None,
            "source": "aggregate(shadow_ledger,backtests)",
            "metrics": out}
