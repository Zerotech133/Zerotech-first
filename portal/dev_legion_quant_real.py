# -*- coding: utf-8 -*-
"""作战屏 v1.1 · 策略运行只读聚合（LV-02，2026-10-29）。

只读 `agent_platform/data/strategy/strategy_runs.jsonl`，只出聚合：
策略数 / 最近运行时间 / 近 7 天决策数 / 多空中性计数。
**不读取/不透出** symbol、rationale、逐条 recommendation；分桶 n<5 显示 "<5"。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "agent_platform" / "data" / "strategy" / "strategy_runs.jsonl"
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


def build_quant_real(root=None, days=7, now=None):
    """返回 {as_of, source, window_days, metrics}；无数据返回 None。"""
    root = Path(root or ROOT)
    rows = _read_rows(root / "agent_platform" / "data" / "strategy"
                      / "strategy_runs.jsonl")
    if not rows:
        return None
    now = now or time.time()
    cut = time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))
    recent = []
    for r in rows:
        stamp = str(r.get("recorded_at") or r.get("trade_date") or "")
        if stamp[:10] >= cut:
            recent.append(r)
    if not recent:
        recent = rows[-200:]  # 真源无近期数据时退化为最近样本，仍只出聚合
    strategies = {str(r.get("strategy_id") or "") for r in recent}
    strategies.discard("")
    stance = {"buy": 0, "sell": 0, "hold": 0}
    last = ""
    for r in recent:
        last = max(last, str(r.get("recorded_at") or r.get("trade_date") or ""))
        rec = str(r.get("recommendation") or "").lower()
        if rec in ("buy", "sell", "hold"):
            stance[rec] += 1
        elif rec in ("long", "bull"):
            stance["buy"] += 1
        elif rec in ("short", "bear"):
            stance["sell"] += 1
        else:
            stance["hold"] += 1
    return {
        "as_of": last or None,
        "source": "aggregate(strategy_runs)",
        "window_days": days,
        "metrics": {
            "strategies": _mask(len(strategies)),
            "runs_window": len(recent),
            "last_run_at": last or None,
            "stance": {"buy": _mask(stance["buy"]),
                       "sell": _mask(stance["sell"]),
                       "hold": _mask(stance["hold"])},
        },
    }
