# -*- coding: utf-8 -*-
"""最近滚动年四腿的季度拆解：v3 在弱市段的亏损到底从哪来。

老田 2026-11-10：「四条腿按季度拆开看 v3 在弱市段的具体亏损来源
（TP 触发率 vs 止损）」。

方法：
  1. 重跑 bt10m_1y_recent 的四条腿，payload 落盘 data/bt2/bt2_1yq_*.json
     （下次分析直接读文件，不再重跑）；
  2. 逐笔按「卖出日」归季度（实现损益的归属期），按退出原因分桶：
     take_profit / stop_loss / trail_stop / time_stop(到期) / 3b_fail / ma_break / exit_open；
  3. 每腿每季出：n、笔均 pnl、胜率、TP 触发率、各退出原因的笔数与笔均损益；
  4. equity 曲线按季切收益，与等权基准同季对照。

注意：pnl 字段是「单笔收益率 %」，对组合的贡献 ≈ pnl × slot_pct
（v3 固定槽 10%、d_legacy 8%），组合层面以 equity 季度收益为准。

用法：python bt10m_1y_quarters.py
产出：reports/bt10m_1y_quarters.json
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
OUT = os.path.join(BASE, "reports", "bt10m_1y_quarters.json")

D0, D1 = "20251110", "20261109"
COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]
LEGS = [("v3", "30bp"), ("v3", "122bp"), ("d_legacy", "30bp"), ("d_legacy", "122bp")]


def quarter(day):
    """YYYYMMDD → 2025Q4 / 2026Q1 ..."""
    y, m = int(day[:4]), int(day[4:6])
    return "%dQ%d" % (y, (m - 1) // 3 + 1)


def load_or_run(leg, cost_tag, cost):
    """先读落盘 payload，没有才跑（跑完必落盘）。"""
    fp = os.path.join(BASE, "data", "bt2", "bt2_1yq_%s_%s.json" % (leg, cost_tag))
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    p = {"preset": leg, "d0": D0, "d1": D1, "cost": cost}
    payload, err = eng.run_job(p, "1yq_%s_%s" % (leg, cost_tag))
    if err or not payload:
        raise SystemExit("%s@%s 跑失败：%s" % (leg, cost_tag, err))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump(payload, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    return payload


def pair_trades(trades):
    """BUY/SELL 配对 → [(买日, 卖日, pnl%, how)]"""
    q, out = {}, []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t)
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst:
                b = lst.pop(0)
                if t.get("pnl") is not None:
                    out.append((b["day"], t["day"], float(t["pnl"]), t.get("how", "?")))
    return out


def exit_breakdown(pairs, d_from=None, d_to=None):
    """按退出原因分桶。d_from/d_to 过滤卖出日。"""
    buckets = {}
    for bd, sd, pnl, how in pairs:
        if d_from and sd < d_from or d_to and sd > d_to:
            continue
        b = buckets.setdefault(how, {"n": 0, "pnls": []})
        b["n"] += 1
        b["pnls"].append(pnl)
    for how, b in buckets.items():
        xs = b["pnls"]
        b["avg_pnl"] = round(statistics.mean(xs), 2)
        b["sum_pnl"] = round(sum(xs), 2)
        b["win_pct"] = round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1)
        del b["pnls"]
    return buckets


def quarter_stats(pairs, eq_curve, nav, ks):
    """逐季：equity 收益 + 逐笔分桶 + TP 触发率。"""
    qs = sorted({quarter(d) for d in ks})
    # equity 季末序列（首季的基期 = 1.0，equity 曲线从窗口起点起算）
    eq_last = {}
    for row in eq_curve:
        eq_last[quarter(row["day"])] = row["equity"]
    # 基准季度收益
    qbench_last = {}
    for k in ks:
        qbench_last[quarter(k)] = nav[k]
    qlist = sorted(set(eq_last) | set(qbench_last))
    out = []
    for qq in qlist:
        # 本季卖出的逐笔
        qp = [x for x in pairs if quarter(x[1]) == qq]
        row = {
            "q": qq,
            "eq_ret": None, "bench_ret": None,
            "n": len(qp),
            "avg_pnl": round(statistics.mean([x[2] for x in qp]), 2) if qp else None,
            "win_pct": round(100.0 * sum(1 for x in qp if x[2] > 0) / len(qp), 1) if qp else None,
            "tp_rate": round(100.0 * sum(1 for x in qp if x[3] == "take_profit") / len(qp), 1) if qp else None,
            "stop_rate": round(100.0 * sum(1 for x in qp if x[3] in ("stop_loss", "trail_stop")) / len(qp), 1) if qp else None,
            "by_exit": exit_breakdown(qp),
        }
        # 季内收益 = 季末/上季末 - 1（首季基期 1.0）
        def _qret(series_last, q):
            idx = qlist.index(q)
            prev_q = qlist[idx - 1] if idx > 0 else None
            cur = series_last.get(q)
            if cur is None:
                return None
            base = series_last.get(prev_q, 1.0) if prev_q else 1.0
            return round((cur / base - 1) * 100, 2)
        row["eq_ret"] = _qret(eq_last, qq)
        row["bench_ret"] = _qret(qbench_last, qq)
        out.append(row)
    return out


def main():
    nav, n = bt10m.load_market_bench(D0, D1)
    ks = sorted(nav)
    res = {"window": {"d0": D0, "d1": D1, "days": len(ks),
                      "bench_total": round((nav[ks[-1]] / nav[ks[0]] - 1) * 100, 2)},
           "legs": {}}

    for leg, cost_tag in LEGS:
        cost = {tag: c for c, tag in COSTS}[cost_tag]
        t0 = time.time()
        payload = load_or_run(leg, cost_tag, cost)
        pairs = pair_trades(payload["trades"])
        key = "%s@%s" % (leg, cost_tag)
        res["legs"][key] = {
            "total_ret": round((payload["equity_curve"][-1]["equity"] - 1) * 100, 2),
            "n_trades": len(pairs),
            "exit_all": exit_breakdown(pairs),
            "quarters": quarter_stats(pairs, payload["equity_curve"], nav, ks),
            "secs": round(time.time() - t0, 1),
        }
        # 控制台：逐季一行 + 全年退出分桶
        print("\n== %s  全年 %+0.2f%%  %d 笔 ==" % (key, res["legs"][key]["total_ret"], len(pairs)))
        print("  退出分桶: " + "  ".join(
            "%s n=%d avg=%+.2f%% win=%.0f%%" % (h, b["n"], b["avg_pnl"], b["win_pct"])
            for h, b in sorted(res["legs"][key]["exit_all"].items(),
                               key=lambda kv: -kv[1]["n"])))
        for r in res["legs"][key]["quarters"]:
            print("  %s  组合 %+7.2f%%  基准 %+7.2f%%  %3d 笔  笔均 %+6.2f%%  "
                  "胜率 %5.1f%%  TP触发 %5.1f%%  止损 %5.1f%%"
                  % (r["q"], r["eq_ret"] or 0, r["bench_ret"] or 0, r["n"],
                     r["avg_pnl"] or 0, r["win_pct"] or 0,
                     r["tp_rate"] or 0, r["stop_rate"] or 0), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)


if __name__ == "__main__":
    main()
