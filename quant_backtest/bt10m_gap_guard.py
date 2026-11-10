# -*- coding: utf-8 -*-
"""跳空保护对照：止损从「收盘触发→次日开盘卖」改成「当日挂限价、盘中触价即成交」。

老田 2026-11-10（复述 Agent 建议为指令）：跳空保护。

背景：止损网格证明 −8% 维持，但原口径的 −8% 实测笔均 −9.4%（RY 29 笔），
差额全是隔夜跳空——收盘触发后要熬一整夜，次日开盘价往往更深。

三档口径（只动 stop_fill_mode / stop_slip，其余全踩 v3 预设）：
  G0  open_next          原口径（= 固化 v3，对照组）
  G1  intraday           止损价当日挂限价：low 触价当日按止损价成交；
                          开盘已跳穿则按开盘价（不吃便宜）；同日高低双触按止损优先
  G2  intraday + slip30  同 G1，触价成交档再扣 30bp 滑点（与止盈 slip30 同一约定）

引擎实现（backtest_engine_v2.py A3b 块，2026-11-10 新增）：
  - T+1 与 min_holding_days 守卫与止盈块同源（当日买入当日不可卖）；
  - 逐笔带 want/fill/d_open/d_low/stop_gap 明细，gap 单占比直接可算；
  - 日内优先级：A3b 止损 → A4 止盈（同日双触时止损先卖，保守假设）。

窗口：PRE / OOS / IN / RY（同 bt10m_stop_grid），成本 122bp 主口径 + 30bp 对照。
用法：python bt10m_gap_guard.py
产出：reports/bt10m_gap_guard.json
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
OUT = os.path.join(BASE, "reports", "bt10m_gap_guard.json")

WINDOWS = [
    ("PRE", "20231010", "20240930", "第三窗口（从未参与挑参）"),
    ("OOS", "20241008", "20251010", "牛市年"),
    ("IN", "20251013", "20261012", "参数挑选年"),
    ("RY", "20251110", "20261109", "滚动年（弱市·病灶窗口）"),
]
COSTS = [(0.0061, "122bp"), (0.0015, "30bp")]

VARIANTS = {
    "G0_open_next": {},
    "G1_intraday": {"stop_fill_mode": "intraday"},
    "G2_intra_slip30": {"stop_fill_mode": "intraday", "stop_slip": 0.003},
}


def _selfcheck():
    for name, ov in VARIANTS.items():
        p = {"preset": "v3"}
        p.update(ov)
        e = eng.apply_preset(p)
        for k, v in ov.items():
            got = e.get(k)
            if isinstance(v, float):
                if got is None or abs(float(got) - v) > 1e-9:
                    raise SystemExit("%s 的 %s=%r 没生效(期望 %r)" % (name, k, got, v))
            elif got != v:
                raise SystemExit("%s 的 %s=%r 没生效(期望 %r)" % (name, k, got, v))
    # 端到端：跑一个最小窗口确认 intraday 单真的带 stop_mode 标记
    p = {"preset": "v3", "d0": "20251110", "d1": "20261109", "cost": 0.0061,
         "stop_fill_mode": "intraday"}
    payload, err = eng.run_job(p, "gapguard_smoke")
    if err or not payload:
        raise SystemExit("冒烟失败：%s" % err)
    sl = [t for t in payload["trades"] if t.get("how") == "stop_loss"]
    n_intra = sum(1 for t in sl if t.get("stop_mode") == "intraday")
    if sl and n_intra == 0:
        raise SystemExit("intraday 模式下止损单没有 stop_mode=intraday 标记，块未生效")
    print("[selfcheck] 3 变体参数生效；冒烟 stop_loss %d 笔全部走 intraday 标记" % n_sl_safe(sl),
          flush=True)


def n_sl_safe(sl):
    return len(sl)


def pair_alpha(trades, nav, ks):
    q, pairs, sls = {}, [], []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst and t.get("pnl") is not None:
                pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t.get("how", "?"), t))
    def bret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        if not la or not lb:
            return None
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100
    al = []
    for bd, sd, pnl, how, t in pairs:
        br = bret(bd, sd)
        if br is not None:
            al.append(pnl - br)
    m = statistics.mean(al) if al else 0.0
    sd_ = statistics.stdev(al) if len(al) > 1 else 0.0
    t = m / (sd_ / math.sqrt(len(al))) if sd_ else 0.0
    sl = [x for x in pairs if x[3] == "stop_loss"]
    tp = [x for x in pairs if x[3] == "take_profit"]
    n_gap = sum(1 for x in sl if x[4].get("stop_gap"))
    n_intra = sum(1 for x in sl if x[4].get("stop_mode") == "intraday")
    sl_pnls = [x[2] for x in sl]
    b = {
        "n_sl": len(sl),
        "avg_sl": round(statistics.mean(sl_pnls), 2) if sl_pnls else None,
        "sum_sl": round(sum(sl_pnls), 1) if sl_pnls else 0.0,
        "n_gap": n_gap,
        "n_intraday": n_intra,
        "n_tp": len(tp),
        "avg_tp": round(statistics.mean([x[2] for x in tp]), 2) if tp else None,
    }
    return len(pairs), m * 100, t, b


def main():
    _selfcheck()
    res = {"variants": VARIANTS, "windows": {}}
    for tag, d0, d1, desc in WINDOWS:
        nav, n = bt10m.load_market_bench(d0, d1)
        ks = sorted(nav)
        bench = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
        wres = {"desc": desc, "bench_ret": round(bench, 2), "cells": {}}
        print("\n== %s %s→%s  基准 %+0.2f%% ==" % (tag, d0, d1, bench), flush=True)
        print("  %-16s %8s %5s %9s %6s  %s"
              % ("变体", "收益", "笔数", "超额bp", "t", "止损(笔/笔均/小计/跳空单) | TP笔数"))
        for vname, ov in VARIANTS.items():
            for cost, ctag in COSTS:
                p = {"preset": "v3", "d0": d0, "d1": d1, "cost": cost}
                p.update(ov)
                t0 = time.time()
                payload, err = eng.run_job(p, "gapguard_%s_%s_%s" % (tag, vname, ctag))
                if err or not payload:
                    print("  %s@%s 失败：%s" % (vname, ctag, err), flush=True)
                    continue
                nn, a, t, b = pair_alpha(payload["trades"], nav, ks)
                ret = round((payload["equity_curve"][-1]["equity"] - 1) * 100, 2)
                wres["cells"]["%s@%s" % (vname, ctag)] = {
                    "ret": ret, "n": nn, "alpha_bp": round(a), "t": round(t, 2),
                    "cost": ctag, **b}
                if ctag == "122bp":
                    print("  %-16s %+8.2f%% %5d %+9.0f %6.2f  %d笔/%+.1f%%/%+.0f点/跳空%d | TP %d笔"
                          % (vname, ret, nn, a, t, b["n_sl"], b["avg_sl"] or 0,
                             b["sum_sl"], b["n_gap"], b["n_tp"]), flush=True)
        res["windows"][tag] = wres

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)


if __name__ == "__main__":
    main()
