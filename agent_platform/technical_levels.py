# -*- coding: utf-8 -*-
"""D1 研究化建仓水平（2026-10-31 WO-D1-PLAN-20261031）。

从本地日线/ATR/近期支撑压力推导买点、止损、止盈；不接实盘。
优先本地 ashare parquet；无数据返回空（页面回退旧计划），不做写操作。
"""
from __future__ import annotations

import glob
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASHARE = os.path.join(REPO, "quant_backtest", "data", "ashare")
_CACHE = {}
_CACHE_TS = {}
_CACHE_TTL = 300


def _suffix(code):
    c = str(code or "").split(".")[0].strip()
    if c.startswith("6"):
        return "SH"
    if c.startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


def load_bars(code, limit=80):
    """本地 parquet -> [[date, open, close, high, low, vol], ...]；缺失返回 []。"""
    c = str(code or "").split(".")[0].strip()
    files = glob.glob(os.path.join(ASHARE, "%s_%s.parquet" % (c, _suffix(c))))
    if not files:
        return []
    try:
        import pandas as pd
    except ImportError:
        return []
    try:
        df = pd.read_parquet(files[0]).sort_values("trade_date").tail(int(limit))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for _, r in df.iterrows():
        out.append([str(r["trade_date"]), float(r["open"]), float(r["close"]),
                    float(r["high"]), float(r["low"]), float(r["volume"])])
    return out


def _atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = float(bars[i][3]), float(bars[i][4]), float(bars[i - 1][2])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def _ma(bars, n):
    if len(bars) < n:
        return None
    return sum(float(b[2]) for b in bars[-n:]) / n


def derive_from_bars(bars, price=None, can_buy=False, lookback=20):
    """纯函数：给定 bars 推导水平；数据不足返回 {}。"""
    if not bars or len(bars) < lookback + 2:
        return {}
    close = float(bars[-1][2])
    entry = float(price) if price else close
    atr = _atr(bars)
    if not atr or atr <= 0:
        return {}
    ma20 = _ma(bars, 20)
    support = min(float(b[4]) for b in bars[-lookback:])
    resistance = max(float(b[3]) for b in bars[-lookback:])
    # 盯盘触发：回踩优先 MA20/支撑，突破优先近期高点；保证相对现价有方向
    pull = max(support, ma20 or support)
    pull = min(pull, round(entry * 0.995, 2))
    breakout = max(resistance, round(entry * 1.005, 2))
    watch_trigger = {"pullback": round(pull, 2), "breakout": round(breakout, 2)}
    # 止损：支撑下方 0.5 ATR，且不超过 entry - 1.5 ATR
    stop = min(support - 0.5 * atr, entry - 1.5 * atr)
    stop = round(stop, 2)
    if stop >= entry:
        stop = round(entry - 1.5 * atr, 2)
    risk = entry - stop
    if risk <= 0:
        return {}
    target1 = round(entry + 2.0 * risk, 2)
    target2 = round(entry + 3.0 * risk, 2)
    out = {
        "watch_trigger": watch_trigger,
        "stop_loss": stop,
        "target1": target1,
        "target2": target2,
        "atr": round(atr, 2),
        "ma20": round(ma20, 2) if ma20 else None,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "source": "technical_atr_support_resistance",
    }
    if can_buy:
        # 买点区间：支撑/MA20 与现价之间，不追高
        low = max(support, ma20 or support, entry - 0.8 * atr)
        high = min(entry + 0.2 * atr, entry)
        if low >= high:
            low, high = entry - 0.3 * atr, entry
        out["buy_zone"] = [round(low, 2), round(high, 2)]
    return out


def derive(code, price=None, can_buy=False, use_cache=True):
    c = str(code or "").split(".")[0].strip()
    key = (c, round(float(price or 0), 2), bool(can_buy))
    if use_cache and key in _CACHE and time.time() - _CACHE_TS.get(key, 0) < _CACHE_TTL:
        return _CACHE[key]
    bars = load_bars(c)
    out = derive_from_bars(bars, price=price, can_buy=can_buy)
    if use_cache:
        _CACHE[key] = out
        _CACHE_TS[key] = time.time()
    return out
