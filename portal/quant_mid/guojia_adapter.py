# -*- coding: utf-8 -*-
"""K-QUANT-004: 郭嘉·缠论信号适配层 — chan_monitor → trade_pool.

架构:
  ChanMonitorAdapter.score(trade_date)  # 上游缠论三类买卖点规则引擎
    → List[Signal] (symbol/action/confidence/rationale)
    → POST 8097 /api/quant/push         # 推 trade_pool 投研池

不直接 import chan_monitor 内部引擎（保持包装层独立可测）。
不做买卖决策（郭嘉=纯研究层，只推信号不碰决策）。

Usage:
    python portal/quant_mid/guojia_adapter.py --once
    python portal/quant_mid/guojia_adapter.py --date 20261019 --dry-run
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
from typing import List, Optional

# 让 chan_monitor 能被 import
_QB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

import requests  # noqa: E402

from quant_backtest.strategy_layer.adapters.chan_monitor import (  # noqa: E402
    ChanMonitorAdapter,
)

# 8097 地址 (env 优先)
HUB_URL = os.environ.get("QUANT_HUB_URL", "http://127.0.0.1:8097")
API_PUSH = f"{HUB_URL}/api/quant/push"

# 鉴权 key (复用 push_secrets.json / env)
def _hub_key() -> Optional[str]:
    k = os.environ.get("PUSH_HUB_KEY") or os.environ.get("push_hub_key")
    if k:
        return k
    try:
        sp = os.environ.get(
            "PUSH_SECRETS_PATH",
            os.path.join(os.path.dirname(__file__), "..", "work", "push_secrets.json"),
        )
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                return json.load(f).get("push_hub_key")
    except Exception:
        pass
    return None


# action(buy/sell) → trade_pool signal(BUY/SELL)
_ACTION_MAP = {"buy": "BUY", "sell": "SELL"}


def load_chan_signals(trade_date: str) -> List[dict]:
    """拉 chan_monitor 信号池 → 可推 trade_pool 的字典列表.

    trade_date: YYYYMMDD 字符串
    """
    adapter = ChanMonitorAdapter()
    sigs = adapter.score(trade_date)
    out = []
    for s in sigs:
        signal = _ACTION_MAP.get(s.action)
        if not signal:
            continue  # hold/watch 不进 trade_pool (缠论只产出 buy/sell)
        out.append({
            "stock_code": s.symbol,
            "stock_name": "",  # chan_monitor 信号池未稳定提供 name, 投研可补
            "signal": signal,
            "confidence": s.confidence,
            "source_agent": "guojia_chan_monitor",
            "rationale": s.rationale,
        })
    return out


def push_to_trade_pool(items: List[dict], *, dry_run: bool = False) -> dict:
    """批量 POST 8097 /api/quant/push.

    返回 {"ok": True/False, "count": N, "ids": [...], "error": ""}
    """
    if not items:
        return {"ok": True, "count": 0, "ids": [], "error": ""}
    payload = {"items": items}
    if dry_run:
        print(f"[guojia_adapter] DRY-RUN: push {len(items)} items → {API_PUSH}")
        for it in items[:3]:
            print(f"  {it['stock_code']} {it['signal']} conf={it['confidence']}")
        return {"ok": True, "count": len(items), "ids": [], "error": "dry-run"}
    try:
        key = _hub_key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-Push-Hub-Key"] = key
        r = requests.post(API_PUSH, json=payload, headers=headers, timeout=10)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("ok"):
            return {"ok": True, "count": body.get("count", len(items)),
                    "ids": body.get("ids", []), "error": ""}
        return {"ok": False, "count": 0, "ids": [],
                "error": f"HTTP {r.status_code}: {body.get('error', r.text[:200])}"}
    except requests.ConnectionError as e:
        return {"ok": False, "count": 0, "ids": [],
                "error": f"8097 unreachable: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "count": 0, "ids": [], "error": str(e)}


def run(trade_date: Optional[str] = None, *, dry_run: bool = False) -> dict:
    """全链: 拉 chan_monitor → 推 trade_pool. 返回 summary."""
    if trade_date is None:
        trade_date = dt.date.today().strftime("%Y%m%d")
    signals = load_chan_signals(trade_date)
    result = push_to_trade_pool(signals, dry_run=dry_run)
    return {
        "trade_date": trade_date,
        "signal_count": len(signals),
        "push_result": result,
    }


# ── CLI ───────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="郭嘉·缠论信号 → trade_pool")
    ap.add_argument("--once", action="store_true", help="跑一次就退出")
    ap.add_argument("--date", default=None, help="trade_date YYYYMMDD (默认今天)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推")
    args = ap.parse_args()

    if not args.once:
        # 默认也只跑一次 (郭嘉是研究层, 不该定时推 trade_pool;
        # 定时推应由 HERMES 调度触发)
        pass

    summary = run(args.date, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if summary["push_result"]["ok"] else 1)
