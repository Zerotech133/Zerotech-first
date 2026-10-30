# -*- coding: utf-8 -*-
"""首批交易手法规则引擎 + paper 历史回放（2026-10-30 WO-METHODS-20261030）。

- 趋势回踩 trend_pullback
- 放量突破 volume_breakout
- 超跌反弹 oversold_rebound

只做 paper 回放/统计，不连接真实交易；信号在收盘确认，次一交易日开盘成交，
避免同一根 K 线内的未来函数。日K 结构：[[date, open, close, high, low, vol, ...], ...]。
"""
from __future__ import annotations

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
        "trail_pct": 0.05, "take_profit_atr": 3.0,
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
        "trail_pct": 0.06, "take_profit_atr": 3.0,
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
        "trail_pct": 0.03, "take_profit_atr": 2.0,
    },
}

DEFAULT_COSTS = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.0005}
DEFAULT_LIMITS = {"single_max": 0.10, "total_max": 0.60}
REGIME_METHODS = {"trend_pullback", "volume_breakout"}


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


def simulate(method_id, bars, regime_by_date=None, costs=None, exit_opt=True):
    """单标的 paper 回放；regime_by_date 为 None 时不过滤；costs 计入往返成本。"""
    if method_id not in METHODS:
        raise ValueError("unknown method: %s" % method_id)
    trades = []
    i = 25
    max_hold = int(METHODS[method_id]["holding_days"][1])
    while i < len(bars) - 1:
        if regime_by_date is not None and not regime_by_date.get(str(bars[i][0]), True):
            i += 1
            continue
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
        peak_close = entry_price
        base_atr = indicators(bars[: entry_index + 1]).get("atr14")
        trail = float(METHODS[method_id].get("trail_pct", 0.05) or 0.05)
        tp_atr = float(METHODS[method_id].get("take_profit_atr", 3.0) or 0.0)
        for j in range(entry_index + 1, min(entry_index + max_hold + 1, len(bars))):
            ind_j = indicators(bars[: j + 1])
            close_j = ind_j.get("close") or entry_price
            high_j = _f(bars[j][3])
            if exit_opt:
                if tp_atr and base_atr and high_j >= entry_price + tp_atr * base_atr:
                    exit_index, exit_price, reason = j, entry_price + tp_atr * base_atr, "take_profit_atr"
                    break
                peak_close = max(peak_close, close_j)
                if close_j <= peak_close * (1.0 - trail):
                    exit_index, exit_price, reason = j, close_j, "trailing_stop"
                    break
            should, px, why = exit_reason(method_id, bars[: j + 1], entry_price, j)
            if should and px and px > 0:
                exit_index, exit_price, reason = j, px, why
                break
        if exit_index is None:
            exit_index = min(entry_index + max_hold, len(bars) - 1)
            exit_price = _f(bars[exit_index][2])
            reason = "max_hold"
        ret = (exit_price / entry_price - 1.0) if entry_price else 0.0
        cost = float((costs or {}).get("commission", 0.0)) * 2.0 \
            + float((costs or {}).get("slippage", 0.0)) * 2.0 \
            + float((costs or {}).get("stamp", 0.0))
        net = ret - cost
        trades.append({
            "entry_date": str(bars[entry_index][0]), "entry": round(entry_price, 4),
            "exit_date": str(bars[exit_index][0]), "exit": round(exit_price, 4),
            "gross_return_pct": round(ret * 100, 2),
            "return_pct": round(net * 100, 2), "cost_pct": round(cost * 100, 2),
            "reason": reason, "hold_days": exit_index - entry_index,
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



def local_kline(code, days=250):
    """本地 ashare parquet 回退（系统 Python 有 pandas 时）；无文件返回 []。"""
    import os
    code = str(code or "").strip().lower()
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) < 6:
        return []
    digits = digits[:6]
    suffix = "SH" if (code.startswith("sh") or digits.startswith(("6", "9"))) else "SZ"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "quant_backtest", "data", "ashare",
                        "%s_%s.parquet" % (digits, suffix))
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        df = df.sort_values("trade_date").tail(int(days))
        bars = []
        for _, r in df.iterrows():
            ds = str(r["trade_date"])
            ds = "%s-%s-%s" % (ds[:4], ds[4:6], ds[6:8]) if len(ds) == 8 else ds
            bars.append([ds, float(r["open"]), float(r["close"]), float(r["high"]),
                         float(r["low"]), float(r["volume"])])
        return bars
    except Exception:  # noqa: BLE001 缺 pandas/文件坏 → 返回空
        return []


def auto_kline(code, days=250):
    """先网络（Tencent），失败/空则本地 parquet。"""
    try:
        import os
        import sys
        base = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(base, "engine"))
        from tools import kline_daily as _net  # noqa: E402
        bars = _net(code, days)
        if bars:
            return bars
    except Exception:  # noqa: BLE001
        pass
    return local_kline(code, days)


def market_regime(index_bars, ma=20):
    """大盘 regime：date → bool（收盘>MA20 且 MA20 不向下）。指数不足返回空。"""
    if not index_bars or len(index_bars) < ma + 2:
        return {}
    closes = [_f(b[2]) for b in index_bars]
    out = {}
    for i in range(ma, len(index_bars)):
        cur_ma = sum(closes[i - ma + 1:i + 1]) / ma
        prev_ma = sum(closes[i - ma:i]) / ma
        out[str(index_bars[i][0])] = bool(closes[i] > cur_ma and cur_ma >= prev_ma)
    return out


def portfolio_stats(trades_by_code, bars_by_code, costs=None, limits=None):
    """按单票/总仓上限把逐笔交易聚合为 NAV，计成本，返回统计。"""
    costs = costs or DEFAULT_COSTS
    limits = limits or DEFAULT_LIMITS
    single = float(limits.get("single_max", 0.10))
    total = float(limits.get("total_max", 0.60))
    entry_cost = float(costs.get("commission", 0.0)) + float(costs.get("slippage", 0.0))
    exit_cost = float(costs.get("commission", 0.0)) + float(costs.get("slippage", 0.0)) \
        + float(costs.get("stamp", 0.0))

    closes = {}
    dates = set()
    for code, bars in (bars_by_code or {}).items():
        closes[code] = {str(b[0]): _f(b[2]) for b in bars}
        dates.update(closes[code].keys())

    by_entry, by_exit = {}, {}
    for code, trades in (trades_by_code or {}).items():
        for t in trades:
            by_entry.setdefault(t["entry_date"], []).append((code, t))
            by_exit.setdefault(t["exit_date"], []).append((code, t))

    cash = 1.0
    pos = {}
    nav = []
    for d in sorted(dates):
        for code, t in by_exit.get(d, []):
            if code not in pos:
                continue
            p = pos.pop(code)
            cash += p["weight"] * (t["exit"] / p["entry"]) * (1.0 - exit_cost)
        invested = sum(p["weight"] for p in pos.values())
        for code, t in sorted(by_entry.get(d, []), key=lambda x: x[1]["entry_date"]):
            if code in pos:
                continue
            room = min(single, total - invested)
            if room <= 1e-9 or cash <= 1e-9:
                continue
            w = min(room, cash)
            if w <= 1e-9:
                continue
            cash -= w * (1.0 + entry_cost)
            pos[code] = {"weight": w, "entry": t["entry"], "entry_date": t["entry_date"]}
            invested += w
        mv = cash + sum(p["weight"] * (closes[c].get(d, p["entry"]) / p["entry"])
                        for c, p in pos.items())
        nav.append((d, round(mv, 6)))

    peak = 0.0
    max_dd = 0.0
    for _, v in nav:
        peak = max(peak, v)
        max_dd = min(max_dd, (v / peak - 1.0) if peak else 0.0)
    all_trades = [t for trades in (trades_by_code or {}).values() for t in trades]
    wins = sum(1 for t in all_trades if t.get("return_pct", 0) > 0)
    return {
        "trades": len(all_trades),
        "win_rate": round(wins / len(all_trades), 3) if all_trades else None,
        "nav_start": 1.0,
        "nav_end": nav[-1][1] if nav else 1.0,
        "total_return_pct": round((nav[-1][1] - 1.0) * 100, 2) if nav else 0.0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "days": len(nav),
        "limits": {"single_max": single, "total_max": total},
        "costs": dict(costs),
        "nav_series": nav[-5:],
    }


def replay_with_constraints(codes, days=250, methods=None, kline_fn=None,
                            index_kline_fn=None, costs=None, limits=None,
                            index_code="sh000001", sector_regime_by_code=None,
                            exit_opt=True, relative_strength=False):
    """带费用/滑点/仓位约束/大盘 regime 的批量回放；返回每手法组合统计。"""
    if kline_fn is None:
        kline_fn = auto_kline
    if index_kline_fn is None:
        index_kline_fn = kline_fn
    methods = methods or list(METHODS)
    bars_by_code = {}
    for code in codes:
        try:
            bars = kline_fn(code, days)
        except Exception:  # noqa: BLE001
            bars = []
        if bars:
            bars_by_code[str(code)] = bars
    try:
        index_bars = index_kline_fn(index_code, days)
    except Exception:  # noqa: BLE001
        index_bars = []
    regime = market_regime(index_bars)
    relative = {}
    rel_meta = {}
    if relative_strength:
        try:
            from agent_platform import sector_regime as _sr
            relative, rel_meta = _sr.load_relative_strength(bars_by_code)
        except Exception as exc:  # noqa: BLE001
            rel_meta = {"relative_covered": 0, "reason": str(exc)[:120]}
    out = {"days": days, "codes": len(bars_by_code), "regime_days": len(regime),
           "index_code": index_code, "costs": costs or DEFAULT_COSTS,
           "limits": limits or DEFAULT_LIMITS, "methods": {}}
    for m in methods:
        trades_by_code = {}
        for code, bars in bars_by_code.items():
            reg = regime
            try:
                from agent_platform import sector_regime as _sr
                if sector_regime_by_code and code in sector_regime_by_code:
                    reg = _sr.combined(reg, sector_regime_by_code.get(code) or {})
                if relative and code in relative:
                    reg = _sr.combine_all(reg, relative.get(code) or {})
            except Exception:  # noqa: BLE001
                reg = regime
            pass_reg = reg if m in REGIME_METHODS else None
            r = simulate(m, bars, regime_by_date=pass_reg,
                         costs=costs or DEFAULT_COSTS, exit_opt=exit_opt)
            if r["trades"]:
                trades_by_code[code] = r["trades"]
        out["methods"][m] = portfolio_stats(trades_by_code, bars_by_code,
                                            costs=costs, limits=limits)
        out["methods"][m]["regime_filter"] = m in REGIME_METHODS
    out["relative"] = rel_meta
    return out

