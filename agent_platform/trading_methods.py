# -*- coding: utf-8 -*-
"""首批交易手法规则引擎 + paper 历史回放（2026-10-30 WO-METHODS-20261030）。

- 趋势回踩 trend_pullback
- 放量突破 volume_breakout
- 超跌反弹 oversold_rebound

只做 paper 回放/统计，不连接真实交易；信号在收盘确认，次一交易日开盘成交，
避免同一根 K 线内的未来函数。日K 结构：[[date, open, close, high, low, vol, ...], ...]。
"""
from __future__ import annotations

import json
import math

METHODS = {
    "trend_pullback": {
        "id": "trend_pullback",
        "name": "趋势回踩",
        "entry": "MA5>MA20 且最低价触及 MA20，收盘仍在 MA20 附近（0.99~1.02）",
        "exit": "收盘跌破 MA20，或持有超过 10 日",
        "stop": "entry - 2*ATR14",
        "sizing": "risk_gate 单票≤10%",
        "holding_days": [1, 10],
        "regime": "trend_up",
        "paper_only": True,
    },
    "volume_breakout": {
        "id": "volume_breakout",
        "name": "放量突破",
        "entry": "收盘突破前 20 日最高价，且成交量>前5日均量1.5倍",
        "exit": "收盘跌破 MA10，或持有超过 10 日",
        "stop": "entry - 2*ATR14",
        "sizing": "risk_gate 单票≤10%",
        "holding_days": [1, 10],
        "regime": "trend_up/breakout",
        "paper_only": True,
    },
    "oversold_rebound": {
        "id": "oversold_rebound",
        "name": "超跌反弹",
        "entry": "RSI14<30 且收盘跌破布林下轨（20,2）",
        "exit": "RSI14>50，或收盘回到 MA20，或持有超过 3 日",
        "stop": "entry - 2*ATR14",
        "sizing": "risk_gate 单票≤10%",
        "holding_days": [1, 3],
        "regime": "mean_reversion",
        "paper_only": True,
    },
}


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        high = _f(bars[i][3])
        low = _f(bars[i][4])
        prev_close = _f(bars[i - 1][2])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs)


def _boll(closes, n=20, k=2.0):
    if len(closes) < n:
        return None, None
    mid = sum(closes[-n:]) / n
    var = sum((x - mid) ** 2 for x in closes[-n:]) / n
    std = math.sqrt(var)
    return mid - k * std, mid + k * std


def indicators(bars):
    if not bars:
        return {}
    closes = [_f(b[2]) for b in bars]
    vols = [_f(b[5]) if len(b) > 5 else 0.0 for b in bars]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    lower, upper = _boll(closes)
    return {
        "close": closes[-1], "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "rsi14": _rsi(closes), "atr14": _atr(bars),
        "boll_lower": lower, "boll_upper": upper,
        "vol": vols[-1] if vols else 0.0,
        "vol_ma5": (sum(vols[-6:-1]) / 5) if len(vols) >= 6 else None,
    }


def entry_signal(method_id, bars):
    """收盘后判定，返回 bool；不足历史返回 False。"""
    if method_id not in METHODS or len(bars) < 25:
        return False
    ind = indicators(bars)
    close = ind["close"]
    ma20 = ind["ma20"]
    if method_id == "trend_pullback":
        low = _f(bars[-1][4])
        return bool(ma20 and ind["ma5"] and ind["ma5"] > ma20
                    and low <= ma20 * 1.01 and close >= ma20 * 0.99
                    and close <= ma20 * 1.02)
    if method_id == "volume_breakout":
        prev_high = max(_f(b[3]) for b in bars[-21:-1]) if len(bars) >= 21 else None
        vol_ma5 = ind["vol_ma5"]
        return bool(prev_high and close > prev_high * 1.001
                    and vol_ma5 and ind["vol"] > vol_ma5 * 1.5)
    if method_id == "oversold_rebound":
        return bool(ind["rsi14"] is not None and ind["rsi14"] < 30
                    and ind["boll_lower"] is not None and close < ind["boll_lower"])
    return False


def exit_reason(method_id, bars, entry_price, entry_index):
    """持有中判定（收盘/盘中止损）；返回 (should_exit, price, reason)。"""
    if len(bars) <= entry_index:
        return False, None, ""
    ind = indicators(bars)
    close = ind["close"]
    low = _f(bars[entry_index][4])
    atr = ind["atr14"]
    if atr and low <= entry_price - 2 * atr:
        return True, entry_price - 2 * atr, "stop_atr"
    if method_id == "trend_pullback":
        if ind["ma20"] and close < ind["ma20"]:
            return True, close, "break_ma20"
    elif method_id == "volume_breakout":
        if ind["ma10"] and close < ind["ma10"]:
            return True, close, "break_ma10"
    elif method_id == "oversold_rebound":
        if (ind["rsi14"] is not None and ind["rsi14"] > 50) or (
                ind["ma20"] and close >= ind["ma20"]):
            return True, close, "rebound_done"
    return False, None, ""


def simulate(method_id, bars):
    """单标的 paper 回放；返回 {trades, stats}。信号次日开盘成交，最多持有 N 日。"""
    if method_id not in METHODS:
        raise ValueError("unknown method: %s" % method_id)
    trades = []
    i = 25
    max_hold = int(METHODS[method_id]["holding_days"][1])
    while i < len(bars) - 1:
        if not entry_signal(method_id, bars[: i + 1]):
            i += 1
            continue
        entry_index = i + 1
        if entry_index >= len(bars):
            break
        entry_price = _f(bars[entry_index][1])  # next open
        if entry_price <= 0:
            i += 1
            continue
        exit_index = None
        exit_price = None
        reason = ""
        for j in range(entry_index + 1, min(entry_index + max_hold + 1, len(bars))):
            should, px, why = exit_reason(method_id, bars[: j + 1], entry_price, j)
            if should and px and px > 0:
                exit_index, exit_price, reason = j, px, why
                break
        if exit_index is None:
            exit_index = min(entry_index + max_hold, len(bars) - 1)
            exit_price = _f(bars[exit_index][2])
            reason = "max_hold"
        ret = (exit_price / entry_price - 1.0) if entry_price else 0.0
        trades.append({
            "entry_date": str(bars[entry_index][0]), "entry": round(entry_price, 4),
            "exit_date": str(bars[exit_index][0]), "exit": round(exit_price, 4),
            "return_pct": round(ret * 100, 2), "reason": reason,
            "hold_days": exit_index - entry_index,
        })
        i = exit_index + 1
    return {"method": method_id, "trades": trades, "stats": _stats(trades)}


def _stats(trades):
    if not trades:
        return {"trades": 0, "win_rate": None, "avg_return_pct": None,
                "total_return_pct": 0.0, "max_drawdown_pct": None}
    rets = [t["return_pct"] / 100.0 for t in trades]
    wins = sum(1 for r in rets if r > 0)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return {"trades": len(trades), "win_rate": round(wins / len(trades), 3),
            "avg_return_pct": round(sum(rets) / len(rets) * 100, 2),
            "total_return_pct": round(cum * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2)}


def replay(codes, days=250, methods=None, kline_fn=None):
    """批量 paper 回放；kline_fn 可注入（测试/离线）。"""
    if kline_fn is None:
        import os
        import sys
        base = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(base, "engine"))
        from tools import kline_daily as kline_fn  # noqa: E402
    methods = methods or list(METHODS)
    out = {"days": days, "methods": {}, "per_code": {}}
    for m in methods:
        out["methods"][m] = {"trades": 0, "wins": 0, "sum_ret": 0.0,
                             "codes": 0, "errors": 0}
    for code in codes:
        try:
            bars = kline_fn(code, days)
        except Exception:  # noqa: BLE001
            bars = []
        out["per_code"][str(code)] = {"bars": len(bars), "methods": {}}
        for m in methods:
            if not bars:
                out["methods"][m]["errors"] += 1
                continue
            r = simulate(m, bars)
            out["per_code"][str(code)]["methods"][m] = r["stats"]
            st = out["methods"][m]
            st["codes"] += 1
            st["trades"] += r["stats"]["trades"]
            if r["stats"]["win_rate"] is not None:
                st["wins"] += round(r["stats"]["win_rate"] * r["stats"]["trades"])
            st["sum_ret"] += r["stats"]["total_return_pct"]
    for m, st in out["methods"].items():
        st["win_rate"] = round(st["wins"] / st["trades"], 3) if st["trades"] else None
        st["avg_code_return_pct"] = round(st["sum_ret"] / st["codes"], 2) if st["codes"] else None
    return out
