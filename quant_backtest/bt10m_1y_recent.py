# -*- coding: utf-8 -*-
"""最近一年滚动窗口回测（2025-11-10 → 2026-11-09，止于昨日收盘）。

老田 2026-11-10：「我们先回测，回测一年的数据。」

此前三个窗口（PRE/IN/OOS）最晚止于 2026-10-12；影子台账昨天刚扫完的
滚动年（20251110→20261109）把数据推进到最近一个交易日。本脚本在同一
套尺子上补这一年的数字：

  腿 1  v3      @ 30bp  —— 固化默认（Z1 = TP20/60日/-8% + regime_8050 + slip30）
  腿 2  v3      @ 122bp —— 真实资金口径（11-08 实测往返）
  腿 3  d_legacy @ 122bp —— 旧 D 方案对照（影子台账的另一条腿）
  基准  等权全A、β=1（与 bt10m_align 同源）

每个腿都折算成「笔均超额 bp + t」，任何收益数字不裸报。

事件缓存：data/sig_events/sig_events_market_v3_20251110_20261109.json
（2026-11-10 为影子台账扫描，13171 事件，含量纲修复后的评分）。

用法：python bt10m_1y_recent.py
产出：reports/bt10m_1y_recent.json
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
OUT = os.path.join(BASE, "reports", "bt10m_1y_recent.json")

D0, D1 = "20251110", "20261109"
COSTS = [(0.0015, "30bp"), (0.0061, "122bp")]

LEGS = [
    ("v3", "30bp"),
    ("v3", "122bp"),
    ("d_legacy", "30bp"),
    ("d_legacy", "122bp"),
]

# 启动自检：v3 预设的关键字段必须真的生效（本项目复发率最高的 bug 族）。
def _selfcheck():
    eff = eng.apply_preset({"preset": "v3"})
    expect = {
        "tp_pct": 0.20, "holding_max_days": 60, "stop_pct": -0.08,
        "max_ext_pct": 0.03, "three_fail_exit": False,
        "cap_mode": "regime_8050", "tp_fill_mode": "slip", "tp_slip": 0.003,
        "top_n": 10, "slot_pct": 0.10, "slot_mode": "fixed",
    }
    bad = []
    for k, v in expect.items():
        got = eff.get(k)
        if isinstance(v, float):
            if got is None or abs(float(got) - v) > 1e-9:
                bad.append("%s=%r(期望 %r)" % (k, got, v))
        elif got != v:
            bad.append("%s=%r(期望 %r)" % (k, got, v))
    if bad:
        raise SystemExit("v3 预设没有真正生效，拒绝跑批：" + "；".join(bad))
    print("[selfcheck] v3 预设 11 个关键字段全部生效", flush=True)


def bench_nav_window():
    nav, n = bt10m.load_market_bench(D0, D1)
    ks = sorted(nav)
    peak, mdd = nav[ks[0]], 0.0
    for k in ks:
        peak = max(peak, nav[k])
        mdd = min(mdd, nav[k] / peak - 1)
    return nav, ks, n, mdd


def main():
    _selfcheck()
    t0 = time.time()
    nav, ks, n_stocks, bench_mdd = bench_nav_window()
    bench_total = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
    print("窗口 %s→%s：%d 个交易日 | 等权基准 %+0.2f%% 回撤 %.2f%%（装载 %.0fs）"
          % (ks[0], ks[-1], len(ks), bench_total, bench_mdd * 100,
             time.time() - t0), flush=True)

    def bench_ret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        if not la or not lb:
            return None
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100

    res = {
        "window": {"d0": D0, "d1": D1, "days": len(ks),
                   "bench_ret_pct": round(bench_total, 2),
                   "bench_mdd_pct": round(bench_mdd * 100, 2),
                   "bench_n": n_stocks},
        "legs": {},
    }

    for leg, cost_tag in LEGS:
        cost = {tag: c for c, tag in COSTS}[cost_tag]
        p = {"preset": leg, "d0": D0, "d1": D1, "cost": cost}
        t1 = time.time()
        payload, err = eng.run_job(p, "1y_recent_%s_%s" % (leg, cost_tag))
        if err or not payload:
            print("%s@%s 失败：%s" % (leg, cost_tag, err), flush=True)
            res["legs"]["%s@%s" % (leg, cost_tag)] = {"error": str(err)}
            continue

        # 配对成 (买日, 卖日, pnl)，逐笔减去同窗等权基准 → 超额
        q, pairs = {}, []
        for t in payload["trades"]:
            if t["side"] == "BUY":
                q.setdefault(t["stock"], []).append(t["day"])
            elif t["side"] == "SELL":
                lst = q.get(t["stock"])
                if lst:
                    pairs.append((lst.pop(0), t["day"], t["pnl"]))
        pairs = [x for x in pairs if x[2] is not None]
        al = []
        for bd, sd, pnl in pairs:
            br = bench_ret(bd, sd)
            if br is not None:
                al.append(float(pnl) - br)
        m = statistics.mean(al) if al else 0.0
        sd_ = statistics.stdev(al) if len(al) > 1 else 0.0
        t = m / (sd_ / math.sqrt(len(al))) if sd_ else 0.0
        s = payload["summary"]
        row = {
            "ret_pct": round((payload["equity_curve"][-1]["equity"] - 1) * 100, 2),
            "mdd_pct": s.get("max_drawdown_pct"),
            "n_trades": len(pairs),
            "avg_pnl_pct": round(statistics.mean([float(x[2]) for x in pairs]), 2) if pairs else None,
            "win_pct": s.get("win_rate_pct"),
            "pf": s.get("profit_factor"),
            "avg_inv_pct": s.get("avg_invested_pct"),
            "alpha_bp": round(m * 100),
            "t": round(t, 2),
            "secs": round(time.time() - t1, 1),
        }
        res["legs"]["%s@%s" % (leg, cost_tag)] = row
        print("%-14s %+8.2f%%  回撤 %6.2f%%  %4d 笔  笔均超额 %+6.0fbp  t=%5.2f  "
              "胜率 %5.1f%%  PF %5s  仓位 %5s%%  (%.0fs)"
              % ("%s@%s" % (leg, cost_tag), row["ret_pct"], row["mdd_pct"] or 0,
                 row["n_trades"], row["alpha_bp"], row["t"],
                 row["win_pct"] or 0, row["pf"], row["avg_inv_pct"],
                 row["secs"]), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)


if __name__ == "__main__":
    main()
