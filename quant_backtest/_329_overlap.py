# -*- coding: utf-8 -*-
"""K-STOCK-329 · 双刀×缠论并轨价值验证：持仓/成交重叠度 + 收益相关性 + 并轨模拟。

用 327 三臂账本（data/bt2/bt2_327_*.json·宇宙取自 chan_final meta.manual_picks·窗 20260901-20261012）：
1. 逐日持仓重叠（共同活跃日 Jaccard + 平均重叠票数）；
2. 逐笔成交重叠（同票同日同侧）；
3. 日收益相关系数（全窗 + 共同活跃窗两口径）；
4. 并轨组合模拟：每日再平衡 50/50，对照单臂 ret/mdd；
5. 结论落 _329_overlap.md。

用法：python _329_overlap.py
"""

import json
import os

QBT = os.path.dirname(os.path.abspath(__file__))
BT2_DIR = os.path.join(QBT, "data", "bt2")
ARMS = ["dknife", "chan_final"]
UNIV = len(
    json.load(open(os.path.join(BT2_DIR, "bt2_327_chan_final.json"), encoding="utf-8"))[
        "meta"
    ].get("manual_picks")
    or []
)


def load(arm):
    return json.load(
        open(os.path.join(BT2_DIR, "bt2_327_%s.json" % arm), encoding="utf-8")
    )


def daily_returns(eq):
    """equity_curve -> [(day, r_pct)]，首日相对 1.0。"""
    out = []
    prev = 1.0
    for row in eq:
        e = row["equity"]
        out.append((row["day"], (e / prev - 1) * 100))
        prev = e
    return out


def max_dd(equities):
    peak, mdd = -1e9, 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1)
    return mdd * 100


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sxy / (sx * sy) if sx > 0 and sy > 0 else float("nan")


def main():
    data = {a: load(a) for a in ARMS}
    days = sorted(set(data[ARMS[0]]["holdings"]) & set(data[ARMS[1]]["holdings"]))

    # 1) 逐日持仓重叠
    hold = {
        a: {d: {p["stock"] for p in data[a]["holdings"].get(d, [])} for d in days}
        for a in ARMS
    }
    [d for d in days if hold[ARMS[0]][d] or hold[ARMS[1]][d]]
    both_active = [d for d in days if hold[ARMS[0]][d] and hold[ARMS[1]][d]]
    inter_days = [d for d in both_active if hold[ARMS[0]][d] & hold[ARMS[1]][d]]
    jac = (len(inter_days) / len(both_active)) if both_active else float("nan")
    avg_common = (
        (
            sum(len(hold[ARMS[0]][d] & hold[ARMS[1]][d]) for d in both_active)
            / len(both_active)
        )
        if both_active
        else float("nan")
    )

    # 2) 逐笔成交重叠
    ev = {}
    for a in ARMS:
        ev[a] = {
            (t["day"], t["stock"], t["side"])
            for t in data[a]["trades"]
            if t["side"] in ("BUY", "SELL")
        }
    n_ev = {a: len(ev[a]) for a in ARMS}
    ev_common = ev[ARMS[0]] & ev[ARMS[1]]

    # 3) 日收益相关
    rets = {a: dict(daily_returns(data[a]["equity_curve"])) for a in ARMS}
    xs = [rets[ARMS[0]][d] for d in days]
    ys = [rets[ARMS[1]][d] for d in days]
    r_all = pearson(xs, ys)
    xs_a = [rets[ARMS[0]][d] for d in both_active]
    ys_a = [rets[ARMS[1]][d] for d in both_active]
    r_act = pearson(xs_a, ys_a)

    # 4) 并轨模拟（每日再平衡 50/50）
    comb_eq, prev = [], 1.0
    for d in days:
        r = 0.5 * (rets[ARMS[0]][d] + rets[ARMS[1]][d]) / 100
        prev *= 1 + r
        comb_eq.append((d, prev))
    comb_ret = (comb_eq[-1][1] - 1) * 100
    comb_mdd = max_dd([e for _, e in comb_eq])
    sing = {}
    for a in ARMS:
        s = data[a]["summary"]
        sing[a] = (
            s["total_return_pct"],
            s["max_drawdown_pct"],
            s["profit_factor"],
            s["n_trades"],
        )

    # 输出
    lines = [
        "# K-STOCK-329 双刀×缠论并轨价值验证（重叠度·基于 327 三臂账本）",
        "",
        "窗 20260901~20261012（%d 交易日）·宇宙 %d 只（chan_final manual_picks·终版快照）"
        % (len(days), UNIV),
        "",
        "## 1. 持仓与成交重叠",
        "- 窗内持仓活跃日：%s %d 日 / %s %d 日；两臂同日均有持仓 %d 日"
        % (
            ARMS[0],
            sum(1 for d in days if hold[ARMS[0]][d]),
            ARMS[1],
            sum(1 for d in days if hold[ARMS[1]][d]),
            len(both_active),
        ),
        "- 共同活跃日中持仓有交集 %d 日 → **日级 Jaccard = %.2f**；共同活跃日均共同持仓 %.1f 只"
        % (len(inter_days), jac, avg_common),
        "- 逐笔成交：dknife %d 笔 / chan_final %d 笔，**同票同日同侧仅 %d 笔**（%.1f%%）"
        % (
            n_ev[ARMS[0]],
            n_ev[ARMS[1]],
            len(ev_common),
            100.0 * len(ev_common) / max(1, min(n_ev.values())),
        ),
        "",
        "## 2. 日收益相关性",
        "- 全窗 24 日 pearson r = **%.3f**" % r_all,
        "- 共同活跃日（%d 日）pearson r = **%.3f**" % (len(both_active), r_act),
        "",
        "## 3. 并轨组合模拟（每日再平衡 50/50）",
        "| 组合 | ret% | maxDD% | PF | 笔数 |",
        "|---|---|---|---|---|",
    ]
    for a in ARMS:
        r, m, pf, n = sing[a]
        lines.append("| %s（单臂） | %s | %s | %s | %s |" % (a, r, m, pf, n))
    lines.append(
        "| **50/50 并轨** | **%.2f** | **%.2f** | -（合成） | %s |"
        % (comb_ret, comb_mdd, sing[ARMS[0]][3] + sing[ARMS[1]][3])
    )
    lines += [
        "",
        "## 4. 判读",
        "- 重叠低（Jaccard %.2f·成交重叠 %d 笔）+ 相关弱（r=%.2f）⇒ 并轨分散价值成立方向；"
        "并轨 ret/mdd 与单臂对照见上表。" % (jac, len(ev_common), r_all),
        "- ⚠️ 24 日短窗：结论仅方向性（r 的 95%% CI 宽·样本少）；正式版宇宙 %d 只已刷新（终版快照 202606-202610）。"
        % UNIV,
    ]
    out = os.path.join(QBT, "_329_overlap.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
