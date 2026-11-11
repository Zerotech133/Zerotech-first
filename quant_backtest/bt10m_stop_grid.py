# -*- coding: utf-8 -*-
"""止损网格 × 三+1窗口：v3 的「固定 −8%」要不要动。

老田 2026-11-10（复述 Agent 建议为指令）：把 v3 止损改「trail 前置」或放宽 −10%，
开一组对照（止损网格 × 三窗口）验证后再决定；影子台账不动。

背景（滚动年拆解 bt10m_1y_quarters 的结论）：
  v3 全年 58 笔里 29 笔(50%)走止损、笔均 −9.42%（深于 −8% 设定值 → 隔夜跳空），
  把 15 笔 TP 的 +295 点几乎吃光；d_legacy 的 trail（+5% 布防/3.5% 回撤）笔均 +4.77%、
  胜率 96%，起了「提前止血」作用。

引擎语义（backtest_engine_v2.py L1467-1503，写变体前必读）：
  - trail 只在 trail_activate>0 且 trail_drawdown>0 时启用，activate=0 = 整支关闭；
  - 布防时峰值 = 布防当日收盘（≥ entry×(1+activate)），之后峰值只升不降；
  - 收盘 ≤ 峰值×(1−drawdown) → 明日开盘卖（与硬止损同为隔夜执行）；
  - 因此「trail 前置」的有效地板 ≈ entry×(1+activate)×(1−drawdown) − entry。

变体（全部踩在 v3 预设上，只动止损/trail 两个参数）：
  S0  基线        stop −8%，无 trail（= 固化 v3）
  S1  放宽        stop −10%
  S2  更宽        stop −12%
  T1  trail前置松 activate 2% / drawdown 5%，stop −8% 兜底（有效地板 ≈ −3%）
  T2  trail前置紧 activate 1% / drawdown 4%，stop −10% 兜底（有效地板 ≈ −3%）

窗口：PRE(23-10→24-09) / OOS(24-10→25-10) / IN(25-10→26-10) / RY(25-11→26-11 滚动年)。
成本：122bp（真实资金，主口径）+ 30bp（对照）。

用法：python bt10m_stop_grid.py
产出：reports/bt10m_stop_grid.json
"""
import json
import math
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_stop_grid.json")

WINDOWS = [
    ("PRE", "20231010", "20240930", "第三窗口（从未参与挑参）"),
    ("OOS", "20241008", "20251010", "牛市年（v3 占优）"),
    ("IN", "20251013", "20261012", "参数挑选年"),
    ("RY", "20251110", "20261109", "滚动年（弱市·病灶窗口）"),
]
COSTS = [(0.0061, "122bp"), (0.0015, "30bp")]

VARIANTS = {
    "S0_base": {},
    "S1_stop10": {"stop_pct": -0.10},
    "S2_stop12": {"stop_pct": -0.12},
    "T1_trail5": {"trail_activate": 0.02, "trail_drawdown": 0.05, "stop_pct": -0.08},
    "T2_trail4": {"trail_activate": 0.01, "trail_drawdown": 0.04, "stop_pct": -0.10},
}


def _selfcheck():
    # 1) 基线 = v3 预设原样
    eff = eng.apply_preset({"preset": "v3"})
    if abs(float(eff["stop_pct"]) + 0.08) > 1e-9 or float(eff["trail_activate"]) != 0:
        raise SystemExit("基线不是 v3 原样，拒绝跑批")
    # 2) 每个变体的参数必须真的生效（显式键覆盖预设）
    for name, ov in VARIANTS.items():
        p = {"preset": "v3"}
        p.update(ov)
        e = eng.apply_preset(p)
        for k, v in ov.items():
            got = e.get(k)
            if isinstance(v, float):
                if got is None or abs(float(got) - v) > 1e-9:
                    raise SystemExit("%s 的 %s=%r 没生效(期望 %r)，拒绝跑批" % (name, k, got, v))
            elif got != v:
                raise SystemExit("%s 的 %s=%r 没生效(期望 %r)，拒绝跑批" % (name, k, got, v))
    print("[selfcheck] 基线=v3原样 + 5 变体参数全部生效", flush=True)


def pair_alpha(trades, nav, ks):
    """配对 → (n, alpha列表, 退出分桶)"""
    q, pairs = {}, []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst and t.get("pnl") is not None:
                pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t.get("how", "?")))
    def bret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        if not la or not lb:
            return None
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100
    al = []
    for bd, sd, pnl, how in pairs:
        br = bret(bd, sd)
        if br is not None:
            al.append(pnl - br)
    m = statistics.mean(al) if al else 0.0
    sd_ = statistics.stdev(al) if len(al) > 1 else 0.0
    t = m / (sd_ / math.sqrt(len(al))) if sd_ else 0.0
    sl = [p for _, _, p, h in pairs if h == "stop_loss"]
    tp = [p for _, _, p, h in pairs if h == "take_profit"]
    tr = [p for _, _, p, h in pairs if h == "trail_stop"]
    buckets = {
        "n_sl": len(sl), "avg_sl": round(statistics.mean(sl), 2) if sl else None,
        "sum_sl": round(sum(sl), 1) if sl else 0.0,
        "n_tp": len(tp), "avg_tp": round(statistics.mean(tp), 2) if tp else None,
        "sum_tp": round(sum(tp), 1) if tp else 0.0,
        "n_trail": len(tr), "avg_trail": round(statistics.mean(tr), 2) if tr else None,
    }
    return len(pairs), m * 100, t, buckets


def main():
    _selfcheck()
    res = {"variants": {k: v for k, v in VARIANTS.items()}, "windows": {}}
    for tag, d0, d1, desc in WINDOWS:
        nav, n = bt10m.load_market_bench(d0, d1)
        ks = sorted(nav)
        bench = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
        wres = {"desc": desc, "bench_ret": round(bench, 2), "days": len(ks), "cells": {}}
        print("\n== %s %s→%s  基准 %+0.2f%% ==" % (tag, d0, d1, bench), flush=True)
        print("  %-12s %8s %5s %9s %6s  %s" % ("变体", "收益", "笔数", "超额bp", "t",
                                               "止损(笔/笔均/小计) | 止盈笔数"))
        for vname, ov in VARIANTS.items():
            for cost, ctag in COSTS:
                p = {"preset": "v3", "d0": d0, "d1": d1, "cost": cost}
                p.update(ov)
                t0 = time.time()
                payload, err = eng.run_job(p, "stopgrid_%s_%s_%s" % (tag, vname, ctag))
                if err or not payload:
                    print("  %s@%s 失败：%s" % (vname, ctag, err), flush=True)
                    continue
                nn, a, t, b = pair_alpha(payload["trades"], nav, ks)
                ret = round((payload["equity_curve"][-1]["equity"] - 1) * 100, 2)
                cell = {"ret": ret, "n": nn, "alpha_bp": round(a), "t": round(t, 2),
                        "cost": ctag, **b}
                wres["cells"]["%s@%s" % (vname, ctag)] = cell
                if ctag == "122bp":
                    print("  %-12s %+8.2f%% %5d %+9.0f %6.2f  %d笔/%+.1f%%/%+.0f点 | TP %d笔%s"
                          % (vname, ret, nn, a, t,
                             b["n_sl"], b["avg_sl"] or 0, b["sum_sl"], b["n_tp"],
                             (" / trail %d笔 %+.1f%%" % (b["n_trail"], b["avg_trail"] or 0))
                             if b["n_trail"] else ""), flush=True)
        res["windows"][tag] = wres
        print("  （30bp 档已算入 json）", flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)


if __name__ == "__main__":
    main()
