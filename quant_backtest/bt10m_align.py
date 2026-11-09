# -*- coding: utf-8 -*-
"""口径对照工具：把不同日期、不同成本、不同买点集的回测放到同一把尺子上。

2026-11-09 老田：「昨天复盘了一版，和今天的口径好像不一样」→ 本脚本是那次核对的复现入口。

子命令
------
variants [job ...]   把 data/bt2/bt2_<job>.json 折算成「笔均 / 笔均超额(bp) / t」
                     尺子 = 等权全A、β=1（与 11-08 的 A1 口径同源）
grid                 跑 1/2/3买 × {30bp,122bp} 两档成本，外加今日口径，出完整对照
bench                只打印等权基准在窗口内的收益与回撤

用法
----
python bt10m_align.py grid
python bt10m_align.py variants base no3b_m20 ext03_m20_tr
"""
import json
import math
import os
import re
import statistics
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")

D0, D1 = "20251013", "20261012"
# 11-08 实测成本：佣金双边 20bp（7.5 买 / 12.5 卖）+ 单边滑点 51bp = 122bp 往返 → 单边 61bp
COST_1108 = 0.0061
COST_1109 = 0.0015  # 今天用的 15bp 单边 = 30bp 往返

_NAV = None


def bench_nav():
    global _NAV
    if _NAV is None:
        import bt10m
        nav, n = bt10m.load_market_bench(D0, D1)
        _NAV = (nav, sorted(nav), n)
    return _NAV


def bench_ret(d0, d1):
    nav, ks, _ = bench_nav()
    a = [k for k in ks if k <= d0]
    b = [k for k in ks if k <= d1]
    if not a or not b:
        return None
    return (nav[b[-1]] / nav[a[-1]] - 1) * 100


def cmd_bench():
    nav, ks, n = bench_nav()
    peak, mdd = nav[ks[0]], 0.0
    for k in ks:
        peak = max(peak, nav[k])
        mdd = min(mdd, nav[k] / peak - 1)
    print("等权全A基准 %s→%s：%d 个交易日  总收益 %+.2f%%  最大回撤 %.2f%%  样本 %d 只"
          % (ks[0], ks[-1], len(ks), (nav[ks[-1]] / nav[ks[0]] - 1) * 100, mdd * 100, n))


def load_pairs(job):
    fp = os.path.join(BASE, "data", "bt2", "bt2_%s.json" % job)
    if not os.path.exists(fp):
        return None, None
    d = json.load(open(fp, encoding="utf-8"))
    q, pairs = {}, []
    for t in d["trades"]:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            l = q.get(t["stock"])
            if l:
                pairs.append((t["stock"], l.pop(0), t["day"], t["pnl"]))
    pairs = [p for p in pairs if p[3] is not None]
    return d, pairs


def metrics(job, verbose=True):
    d, pairs = load_pairs(job)
    if not d:
        if verbose:
            print("%-22s (无产物)" % job)
        return None
    s = d["summary"]
    pnls, al = [], []
    for st, d0, d1, pnl in pairs:
        p = float(pnl)
        pnls.append(p)
        br = bench_ret(d0, d1)
        if br is not None:
            al.append(p - br)
    if not al:
        return None
    m = statistics.mean(al)
    sd = statistics.stdev(al)
    t = m / (sd / math.sqrt(len(al))) if sd else 0.0
    r = {"job": job, "ret": s["total_return_pct"], "dd": s["max_drawdown_pct"],
         "n": len(pairs), "avg_pnl": statistics.mean(pnls),
         "med_pnl": statistics.median(pnls), "avg_a": m, "med_a": statistics.median(al),
         "t": t, "inv": s.get("avg_invested_pct"), "win": s.get("win_rate_pct"),
         "pf": s.get("profit_factor")}
    if verbose:
        print("%-22s %+7.2f%% %6.2f%% %4d %+7.2f%% %+9.0f %+7.2f%% %6.2f %5.1f%% %5s"
              % (r["job"], r["ret"], r["dd"], r["n"], r["avg_pnl"], r["avg_a"] * 100,
                 r["med_a"], r["t"], r["inv"] or 0, r["pf"]))
    return r


def cmd_variants(jobs):
    print("%-22s %8s %7s %4s %8s %9s %8s %6s %6s %5s"
          % ("变体", "组合", "回撤", "笔数", "笔均", "笔均超额bp", "中位超额", "t", "均仓", "PF"))
    for j in jobs:
        metrics(j)


def cmd_grid():
    import backtest_engine_v2 as eng
    import bt10m_sweep as SW

    over_of = {j: o for j, g, n, o in SW.VARIANTS}
    # 11-08 的决策口径：hold20 强平、关失效线、关追高门槛、关移动止损
    D1108 = {"three_fail_exit": False, "holding_max_days": 20,
             "max_ext_pct": 0.0, "trail_activate": 0.0, "trail_drawdown": 0.0}
    cases = []
    for bf, tag in (("1", "只1买"), ("2", "只2买"), ("3", "只3买")):
        for cost, ctag in ((COST_1109, "30bp"), (COST_1108, "122bp")):
            cases.append(("%s_%s" % (tag, ctag), bf, dict(D1108), cost, "11-08规则"))
    for bf, tag in (("1", "只1买"), ("3", "只3买")):
        for cost, ctag in ((COST_1109, "30bp"), (COST_1108, "122bp")):
            cases.append(("今日口径_%s_%s" % (tag, ctag), bf,
                          dict(over_of["ext03_m20_tr"]), cost, "今日规则"))

    print("%-20s %-9s %5s %8s %7s %4s %6s %5s %6s %7s"
          % ("case", "规则", "成本", "组合", "回撤", "笔数", "胜率", "PF", "换手", "成本%"))
    for tag, bf, over, cost, rule in cases:
        p = dict(SW.BASE_PARAMS)
        p.update(over)
        p["buy_filter"] = bf
        p["cost"] = cost
        payload, err = eng.run_job(p, "align_" + tag, progress=lambda *a, **k: None)
        if err or not payload:
            print("%-20s 失败 %s" % (tag, err))
            continue
        s = payload["summary"]
        turn = 0.0
        for rec in payload["days"].values():
            for a in rec.get("actions") or []:
                if a.get("type") == "buy":
                    m = re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                    if m:
                        turn += float(m.group(1)) / 100
        print("%-20s %-9s %5.0fbp %+7.2f%% %6.2f%% %4d %5s %5s %6.2f %6.2f%%"
              % (tag, rule, cost * 2 * 10000, s["total_return_pct"],
                 s["max_drawdown_pct"], s["n_trades"], s["win_rate_pct"],
                 s["profit_factor"], turn, turn * cost * 2 * 100), flush=True)

    print()
    cmd_variants(["align_" + c[0] for c in cases])


if __name__ == "__main__":
    cmd_ = sys.argv[1] if len(sys.argv) > 1 else "grid"
    if cmd_ == "grid":
        cmd_bench()
        print()
        cmd_grid()
    elif cmd_ == "variants":
        cmd_variants(sys.argv[2:] or ["base", "no3b_m20", "ext03_m20", "ext03_m20_tr"])
    elif cmd_ == "bench":
        cmd_bench()
    else:
        print(__doc__)
