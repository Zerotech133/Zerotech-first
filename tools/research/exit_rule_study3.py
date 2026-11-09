# -*- coding: utf-8 -*-
"""出场规则第三轮：资金约束下的账户层，定出"该用哪个规则"的临界资金。

## 为什么前两轮给不出可落地建议

前两轮得到两个**结论相反**的指标：
- 日批均（每开仓日等权）：hold_20 +1.492% > hold_9 +0.962% > trail_08 +0.857%
- 每持仓日收益：hold_9 +0.1080% > hold_5 +0.0897% > hold_20 +0.0746%

这不是矛盾，是两个不同问题的答案：
- 日批均回答"**如果钱管够**，每次机会能赚多少" → 持越久赚越多（暴露效应）
- 每持日回答"**同样的钱**，周转一次赚多少" → 持越短越划算

真实约束是资金有限：日均 21 笔信号，hold_20 需要 420 个并发槽位，
hold_9 只需 189 个。所以"该用哪个规则"取决于 N（能同时持有几个仓位）。

## 做什么

固定初始资金、固定每笔仓位 = 权益/N、按日推进、逐日盯市。
只改出场规则，信号集合与开仓顺序完全相同（按 entry_date, code 排序）。

扫 N ∈ {40, 80, 160, 320, 640} × 规则 ∈ {hold20, hold9, hold5, trail08, stop08, pool}。

输出年化、最大回撤、夏普、平均在场率、成交笔数。

**核心输出是"临界 N"**：在哪个 N 之上 hold20 开始赢，之下短持有开始赢。
这个阈值把"该用哪个规则"变成"你有多少钱"的函数——这才是可落地的结论。

## 必须带的尾部指标（第一轮漏了）

只看均值会得出"止损无用"的危险结论：止损切的正是左尾，
而均值/中位数对尾部不敏感。所以同时给：
- 单笔 p1/p5 分位、最大单笔亏损、亏损笔均值
- 日批均序列的日级夏普（mean/std）
若某规则均值略低但尾部显著收敛，那是可接受的价格。
"""
import argparse
import json
import os
import sys
import zlib
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import exit_rule_study as S  # noqa: E402
import exit_rule_study2 as S2  # noqa: E402
from portfolio_mtm import load_price_panel  # noqa: E402

TDX = S.TDX
OUT = os.path.join(ROOT, "portal", "exit_rule_study3.json")

NS = (40, 80, 160, 320, 640)
RULES = {
    "hold20": ("hold", 0.0, 0.0, 20),
    "hold9": ("hold", 0.0, 0.0, 9),
    "hold5": ("hold", 0.0, 0.0, 5),
    "trail08": ("trail", 0.08, 0.0, 20),
    "stop08": ("stop", 0.08, 0.0, 20),
}
INIT = 1_000_000.0
TRADING_DAYS = 250


def tail_stats(rows, fld):
    """单笔收益分布的尾部。止损的价值若存在，只可能在这里体现。"""
    v = np.array([r[fld] for r in rows if r.get(fld) is not None], float) * 100
    if not len(v):
        return {}
    loss = v[v < 0]
    return {"mean": round(float(v.mean()), 3), "std": round(float(v.std(ddof=1)), 3),
            "p1": round(float(np.percentile(v, 1)), 2),
            "p5": round(float(np.percentile(v, 5)), 2),
            "p50": round(float(np.percentile(v, 50)), 2),
            "p95": round(float(np.percentile(v, 95)), 2),
            "max_loss": round(float(v.min()), 2),
            "mean_loss": round(float(loss.mean()), 3) if len(loss) else None,
            "skew": round(float(((v - v.mean()) ** 3).mean() / (v.std() ** 3 + 1e-12)), 3)}


def day_sharpe(rows, fld):
    """日批均序列的夏普（按 250/持有天数 粗略年化，仅用于横向比较）。"""
    by = defaultdict(list)
    for r in rows:
        if r.get(fld) is not None:
            by[r["d"]].append(r[fld])
    if len(by) < 30:
        return None
    per = np.array([np.mean(x) for x in by.values()], float)
    sd = float(per.std(ddof=1))
    return round(float(per.mean()) / sd * np.sqrt(TRADING_DAYS), 3) if sd > 0 else None


def run_account(picks, axis, ap_, n_slots):
    """账户层：固定目标仓位比例 f = 1/n_slots、每笔投入 = **当前权益** × f、逐日盯市。

    为什么每笔投入必须随权益变（而不是固定为 INIT/n_slots）
    --------------------------------------------------------
    固定初始资金的口径下，权益涨一倍后实际仓位只剩一半，不同规则被稀释的程度
    取决于它赚多快 → 年化不可比、回撤系统性偏小。改成 equity × f 后杠杆恒定，
    规则间才可比。

    为什么用「cash 不足就跳过」而不是允许透支
    ----------------------------------------
    这是真实的资金约束。不同规则的差别恰恰在于：占用槽位久的规则会更快耗尽现金、
    跳过更多信号。跳过数本身就是结论的一部分。
    """
    by_in = defaultdict(list)
    for p in picks:
        by_in[p["ax_in"]].append(p)
    # 组内顺序必须**与规则无关**。原来按 code 排序会让「信号超出槽位时」总是取
    # 小号代码（000xxx），不同规则的跳过时点不同 → 选到的股票池系统性不同，
    # 年化差异里就混进了「选了哪批票」而不是「怎么出场」。改用 crc32 散列，
    # 确定性、可复现、且与代码大小/价格/规则都无关。
    for d in by_in:
        by_in[d].sort(key=lambda z: (zlib.crc32(z["code"].encode()), z["entry_px"]))

    f = 1.0 / n_slots
    cash = INIT
    pos = []
    nav = []
    exposure = []
    n_trade = 0
    n_skip = 0
    for i, d in enumerate(axis):
        # 1) 结算当日到期（按收盘价，扣卖出成本）
        keep = []
        for q in pos:
            if q["ax_out"] <= i:
                px = q["path"].get(i)
                if px is None:
                    px = q["path"][max(k for k in q["path"] if k <= i)]
                cash += q["shares"] * px * (1.0 - S.SELL_COST)
            else:
                keep.append(q)
        pos = keep
        # 2) 盯市（含当日新开的，先按现金计）
        mv = 0.0
        for q in pos:
            px = q["path"].get(i)
            mv += q["shares"] * px if px is not None else q["shares"] * q["px_in"]
        eq = cash + mv
        nav.append(eq)
        exposure.append(mv / eq if eq > 0 else 0.0)
        # 3) 开新仓：仓位规模按**当日权益**重算，现金不足则跳过
        slot = eq * f
        for p in by_in.get(i, []):
            if cash < slot * 0.999:
                n_skip += 1
                continue
            px_in = p["entry_px"] * (1.0 + S.BUY_COST)
            cash -= slot
            pos.append({"shares": slot / px_in, "path": p["path"], "ax_out": p["ax_out"],
                        "code": p["code"], "px_in": px_in})
            n_trade += 1
    nav = np.array(nav, float)
    ret = nav[1:] / nav[:-1] - 1.0
    yrs = len(axis) / TRADING_DAYS
    cagr = float(nav[-1] / nav[0]) ** (1.0 / yrs) - 1.0 if yrs > 0 and nav[-1] > 0 else -1.0
    peak = np.maximum.accumulate(nav)
    mdd = float((nav / peak - 1.0).min())
    sd = float(ret.std(ddof=1))
    sharpe = float(ret.mean()) / sd * np.sqrt(TRADING_DAYS) if sd > 0 else 0.0
    # 分年：每年内的收益与年内回撤
    yr_stat = {}
    for yr in sorted({axis[i][:4] for i in range(len(axis))}):
        idx = [i for i, dd in enumerate(axis) if dd[:4] == yr]
        if len(idx) < 20:
            continue
        seg = nav[idx[0]:idx[-1] + 1]
        pk = np.maximum.accumulate(seg)
        yr_stat[yr] = {"ret": round(float(seg[-1] / seg[0] - 1.0) * 100, 2),
                       "mdd": round(float((seg / pk - 1.0).min()) * 100, 2)}
    return {"cagr": round(cagr * 100, 2), "mdd": round(mdd * 100, 2),
            "sharpe": round(sharpe, 3), "trades": n_trade, "skipped": n_skip,
            "avg_exposure": round(float(np.mean(exposure)) * 100, 1),
            "final_nav": round(float(nav[-1]), 0), "by_year": yr_stat}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = S.load_pool(a.since)
    axis, _, _ = S2.load_index()
    ap_ = {d: i for i, d in enumerate(axis)}
    say("池 %d 笔 / 日历 %d 日 %s~%s" % (len(rows), len(axis), axis[0], axis[-1]))

    P = load_price_panel({r["code"] for r in rows}, TDX, ohlc=True)
    keep = []
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None or r["d"] not in ap_:
            continue
        r["ei"] = ei
        r["_p"] = p
        keep.append(r)
    rows = keep
    say("可模拟 %d 笔" % len(rows))

    # ---- 逐规则模拟 + 构造账户层所需的路径 ----
    stats, acct = {}, {}
    for nm, sp in RULES.items():
        fld = "ret_" + nm
        picks = []
        for r in rows:
            p, ei = r["_p"], r["ei"]
            xi, px, days, why = S.sim(sp, p["o"], p["h"], p["l"], p["c"], ei, r["entry_px"])
            r[fld] = S.net_ret(r["entry_px"], px)
            r["days_" + nm] = days
            # 路径：从入场到出场（含），日历下标 → 归一后价格
            k = r["entry_px"] / p["c"][ei]
            path = {}
            for j in range(ei, min(xi, len(p["c"]) - 1) + 1):
                dd = p["d"][j]
                if dd in ap_:
                    path[ap_[dd]] = p["c"][j] * k
            if not path:
                continue
            picks.append({"code": r["code"], "entry_px": r["entry_px"],
                          "ax_in": ap_[r["d"]],
                          "ax_out": max(path), "path": path})
        stats[nm] = {"day_avg": None, "days_mean": round(float(np.mean([r["days_" + nm] for r in rows])), 2),
                     "tail": tail_stats(rows, fld), "day_sharpe": day_sharpe(rows, fld)}
        n, avg, win = 0, None, None
        by = defaultdict(list)
        for r in rows:
            by[r["d"]].append(r[fld])
        per = np.array([np.mean(v) for v in by.values()], float)
        stats[nm].update({"days": len(per), "day_avg": round(float(per.mean()) * 100, 3),
                          "day_win": round(float((per > 0).mean()) * 100, 1)})
        acct[nm] = picks
        say("  %-8s 均持 %5.2f 日  日批均 %+.3f%%  日级夏普 %s"
            % (nm, stats[nm]["days_mean"], stats[nm]["day_avg"], stats[nm]["day_sharpe"]))

    # ---- 尾部对照 ----
    say("\n[1] 单笔收益分布（止损的价值只可能体现在这里）")
    say("  %-8s %8s %8s %8s %8s %9s %9s %8s"
        % ("规则", "均值%", "p1", "p5", "p95", "最大亏", "亏损笔均", "偏度"))
    for nm, v in stats.items():
        t = v["tail"]
        say("  %-8s %+8.3f %+8.2f %+8.2f %+8.2f %9.2f %9s %+8.3f"
            % (nm, t["mean"], t["p1"], t["p5"], t["p95"], t["max_loss"],
               ("%.3f" % t["mean_loss"]) if t["mean_loss"] is not None else "-", t["skew"]))
    say("  读法：若 trail08 的 p1/p5/最大亏 明显优于 hold20，则均值损失可能是合理的保险成本")
    say("        若尾部也没收敛，那止损就是纯亏，无任何可取之处")

    # ---- 账户层 ----
    say("\n[2] 账户层：固定 N 个槽位，每笔投入 = 初始资金/N")
    say("  日均信号 %.1f 笔；hold20 需要约 %d 个槽位才能容纳全部信号"
        % (len(rows) / len({r["d"] for r in rows}),
           int(len(rows) / len({r["d"] for r in rows}) * 20)))
    table = {}
    for n in NS:
        table[n] = {}
        say("\n  N = %d（单笔 = 权益的 %.2f%%）" % (n, 100.0 / n))
        say("    %-8s %8s %8s %8s %8s %8s %7s"
            % ("规则", "年化%", "最大回撤%", "夏普", "平均仓位%", "成交/跳过", "卖出"))
        for nm in RULES:
            res = run_account(acct[nm], axis, ap_, n)
            table[n][nm] = res
            tb = res["by_year"]
            say("    %-8s %+8.2f %8.2f %8.3f %8.1f %5d/%-5d %s"
                % (nm, res["cagr"], res["mdd"], res["sharpe"], res["avg_exposure"],
                   res["trades"], res["skipped"],
                   " ".join("%s:%+.0f%%" % (y, v["ret"]) for y, v in tb.items())))

    # ---- 分年回撤（老田最关心的） ----
    say("\n[3] 分年最大回撤（%d 个槽位口径）" % NS[1])
    say("  %-8s %s" % ("规则", " ".join("%10s" % y for y in
                                       sorted(table[NS[1]]["hold20"]["by_year"]))))
    for nm in RULES:
        tb = table[NS[1]][nm]["by_year"]
        say("  %-8s %s" % (nm, " ".join("%9.2f%%" % tb[y]["mdd"] for y in sorted(tb))))

    # ---- 临界 N ----
    say("\n[4] 临界 N：hold20 与资金效率最优规则的胜负分界")
    best_short = max(("hold9", "hold5", "trail08"),
                     key=lambda k: stats[k]["day_avg"] / stats[k]["days_mean"])
    say("  资金效率最优规则 = %s（每持日 %+.4f%%）"
        % (best_short, stats[best_short]["day_avg"] / stats[best_short]["days_mean"]))
    crit = []
    for n in NS:
        h, s = table[n]["hold20"], table[n][best_short]
        win = "hold20" if h["cagr"] > s["cagr"] else best_short
        crit.append({"N": n, "hold20_cagr": h["cagr"], "short_cagr": s["cagr"],
                     "winner": win, "hold20_mdd": h["mdd"], "short_mdd": s["mdd"],
                     "hold20_sharpe": h["sharpe"], "short_sharpe": s["sharpe"],
                     "hold20_expo": h["avg_exposure"], "short_expo": s["avg_exposure"]})
        say("  N=%-4d hold20 年化%+7.2f%% 回撤%7.2f%% 夏普%5.2f 仓位%5.1f%% ｜ "
            "%s 年化%+7.2f%% 回撤%7.2f%% 夏普%5.2f 仓位%5.1f%% → %s"
            % (n, h["cagr"], h["mdd"], h["sharpe"], h["avg_exposure"],
               best_short, s["cagr"], s["mdd"], s["sharpe"], s["avg_exposure"], win))

    # 夏普口径的胜负（风险调整后）
    say("\n  按夏普口径：")
    for c in crit:
        c["winner_sharpe"] = ("hold20" if c["hold20_sharpe"] > c["short_sharpe"]
                              else best_short)
        say("  N=%-4d hold20 %.3f vs %s %.3f → %s 胜"
            % (c["N"], c["hold20_sharpe"], best_short, c["short_sharpe"], c["winner_sharpe"]))

    json.dump({"meta": {"pool": os.path.basename(S.POOL), "since": a.since,
                        "n": len(rows), "init": INIT, "ns": list(NS),
                        "axis": [axis[0], axis[-1]]},
               "stats": stats, "account": {str(k): v for k, v in table.items()},
               "critical": crit, "best_short": best_short},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
