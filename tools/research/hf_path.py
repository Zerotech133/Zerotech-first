# -*- coding: utf-8 -*-
"""hf_path：持仓路径解剖（第十九轮，出场规则第一步）。

## 为什么先做这一步，而不是直接试出场规则

上一轮（十八轮）修完成本口径，得到的数字是「持到 250 日年化净 −0.47%」，
由此推出「现有出场都接不住」。但这个推理链缺了一个环节：

**没有任何人看过这 250 天的路径长什么样。**

出场规则是在路径上切一刀，切哪儿取决于路径形状——是"十天内冲高然后阴跌"，
还是"慢牛一路抬"，切法完全相反。不看路径就设计规则，只能靠直觉，
而直觉在这个样本上已经被证伪过两次（固定持期、持到卖点）。

本脚本只做一件事：**把 6,903 笔 1买的 250 天路径摊开，量形状**。
不测规则，不下结论，只出数。

## 量什么

    1. A(T) 曲线      第 T 天的**毛**超额均值，与 122bp 成本线并列。
                      年化净 = (244/T)×(A(T) − C)，所以 A(T) < C 时无论
                      持多久都是负的——这条线是生死线。
    2. 分位带         每个时点收益的 p10/p25/中位/p75/p90，看分布怎么散开。
    3. 分群画像       按 250 日终值分右尾/中性/左尾，看三群人**分别**在
                      第 5/20/60/120 天长什么样——出场规则要区分的正是这三群。
    4. 峰值日分布     收益峰值出现在第几天。峰值日集中在 60 日内 → 该早走；
                      均匀分布 → 固定持期注定两头挨打。
    5. 回吐率         peak_close − final 的分布，多少比例的利润被吐回去。
    6. 条件期望       第 k 天浮盈处于某个区间时，**之后**到 250 日还能赚多少。
                      这是止损/止盈能不能成立的唯一依据（见下）。
    7. 神仙上界       每笔取路径上的最大值（完美预见），给出任何出场规则
                      都不可能超过的天花板。

## 为什么"条件期望"才是判据

成本是**按笔**收的（进出一次 122bp），不是按天收的。所以：

    提前出场省不下成本，只改变"后面的收益还归不归你"。

止损有用的唯一条件是：触发之后的那段收益期望为负。
若触发之后平均还要涨，止损就是花钱买安慰——成本照付，收益不要了。

同理"让利润奔跑"有用的唯一条件是：浮盈状态下后续收益期望为正。
这条在本样本里很可能不成立，因为第 6 项会显示 alpha 率随持期衰减。

用法::

    python -X utf8 tools/research/hf_path.py
    python -X utf8 tools/research/hf_path.py --slip 20     # 换成本档
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_book as H          # noqa: E402
import hf_sell_exit as SE    # noqa: E402  基准/信号/日历全部复用，不另写一套

ROOT = H.ROOT
OUT = os.path.join(ROOT, "portal", "hf_path.json")

CAP = 250                                     # 统一观察窗（交易日）
KEYS = (1, 2, 3, 5, 10, 15, 20, 30, 40, 60, 80, 100, 120, 150, 180, 200, 220, 250)
# 拉到 500 日时要补的时点：峰值日 45% 落在 181~250 日，说明 250 日这个窗口
# 可能是截短的。不测一次就不知道 A(T) 到底在哪见顶。
KEYS_LONG = (300, 350, 400, 500)


def keys_for(cap):
    ks = [k for k in KEYS if k <= cap]
    ks += [k for k in KEYS_LONG if k <= cap]
    return tuple(ks)

# 分组边界（按**毛**终值分，因为解剖的是结构本身，不是扣完成本后的账）
G_UP = 20.0        # > +20% 右尾
G_DN = -10.0       # < −10% 左尾

# 条件期望表的检查时点与区间
COND_K = (20, 60, 120)
COND_BINS = ((-99.0, -20.0, "≤−20%"), (-20.0, -10.0, "−20~−10%"),
             (-10.0, 0.0, "−10~0%"), (0.0, 10.0, "0~+10%"),
             (10.0, 20.0, "+10~+20%"), (20.0, 999.0, ">+20%"))


# ---------------------------------------------------------------- 基准前缀积
_CP = None


def cum():
    """等权全A 的累计净值序列（前缀积），任意区间 O(1) 取。

    `hf_sell_exit.bench_window_ret` 是逐日累乘，逻辑相同但每次 O(区间长度)。
    本脚本要取 18 个时点 × 6903 笔的区间，必须换成前缀积。**等价性由
    `hf_path_selfcheck` 的 P2 逐位比对**——不靠"看起来一样"。
    """
    global _CP
    if _CP is None:
        dates, ret, pos = SE.bench_ew()
        c = [1.0] * len(dates)
        for i in range(1, len(dates)):
            c[i] = c[i - 1] * (1.0 + ret[i])
        _CP = (dates, c, pos)
    return _CP


def bench_iv(d0, d1, beta=1.0):
    """基准区间收益 × β。区间不可得返回 None（不伪造 0）。"""
    dates, c, pos = cum()
    i, j = pos.get(d0), pos.get(d1)
    if i is None or j is None or j <= i:
        return None
    return (beta if beta else 1.0) * (c[j] / c[i] - 1.0)


# ---------------------------------------------------------------- 单票
def _first_gt(dates, d):
    import bisect
    j = bisect.bisect_right(dates, d)
    return j if j < len(dates) else None


def work(args):
    code, buys, betas, cap, keys = args
    b = H.load_bars(code, "20230901")
    if b is None:
        return code, [], "no_bars"
    dates = b.dates
    n = len(dates)
    out = []
    for d0 in sorted(buys):
        ie = _first_gt(dates, d0)
        if ie is None or ie + cap >= n:
            continue
        px_in = b.px_at_k(ie, ie, "o")
        if not px_in or px_in <= 0:
            continue
        beta = betas.get(d0)
        ed = dates[ie]

        # 逐日收盘路径（相对入场价的毛收益，未扣成本）
        path = [0.0] * (cap + 1)
        for k in range(1, cap + 1):
            px = b.px_at_k(ie + k, ie, "c")
            path[k] = (px / px_in - 1.0) if px and px > 0 else path[k - 1]

        # MFE/MAE 用盘中高低点：真正的极值不在收盘，出场决策看的却是收盘，
        # 两个都要——前者说明"曾经有多少"，后者说明"能不能真拿到"。
        mfe = mae = 0.0
        mfe_k = mae_k = 0
        peak_c, peak_k = path[0], 0
        for k in range(1, cap + 1):
            hi = b.px_at_k(ie + k, ie, "h")
            lo = b.px_at_k(ie + k, ie, "l")
            if hi and hi > 0 and hi / px_in - 1.0 > mfe:
                mfe, mfe_k = hi / px_in - 1.0, k
            if lo and lo > 0 and lo / px_in - 1.0 < mae:
                mae, mae_k = lo / px_in - 1.0, k
            if path[k] > peak_c:
                peak_c, peak_k = path[k], k

        at = {}
        al = {}
        for k in keys:
            at[k] = path[k]
            br = bench_iv(ed, dates[ie + k], beta)
            al[k] = (path[k] - br) if br is not None else None

        oracle_k = max(range(1, cap + 1), key=lambda x: path[x])
        br_o = bench_iv(ed, dates[ie + oracle_k], beta)
        oa = (path[oracle_k] - br_o) if br_o is not None else None
        out.append({
            "code": code, "d0": d0, "ed": ed, "beta": beta,
            "final": path[cap], "at": at, "al": al,
            "mfe": mfe, "mfe_k": mfe_k, "mae": mae, "mae_k": mae_k,
            "peak_c": peak_c, "peak_k": peak_k,
            "oracle": path[oracle_k], "oracle_k": oracle_k, "oracle_a": oa,
        })
    return code, out, None


# ---------------------------------------------------------------- 聚合
def _tstat(xs):
    n = len(xs)
    if n < 3:
        return None
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    if sd == 0:
        return None
    return m / (sd / math.sqrt(n))


def _q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return s[i]


def t_cluster(vals, days):
    """按入场日聚类的 t：先算每日均值，再对日序列求 t。

    **为什么必须聚类**：6,903 笔只有 639 个入场日，前 50 个日子占了 48% 的信号
    （信号在暴跌后扎堆出现）。同一天进场的几十只票共享同一个市场冲击，不是
    独立观测。直接对 6,903 个样本求 t，等于把一次市场事件重复计数几十遍，
    t 会被系统性放大 sqrt(6903/639) ≈ 3.3 倍。

    这里用「等权日」口径：每天贡献一个观测。这与"分 639 次下注、每次下注
    几十只票"的真实决策单位一致。
    """
    by = {}
    for v, d in zip(vals, days):
        by.setdefault(d, []).append(v)
    dm = [statistics.mean(v) for v in by.values()]
    return _tstat(dm), len(dm)


def curve_of(rows, cost_bp, keys=None):
    """A(T) 曲线：每个时点的毛超额均值 + 年化三分。"""
    out = {}
    days = [r["ed"] for r in rows]
    for k in (keys or KEYS):
        tot = [r["at"][k] for r in rows]
        al = [r["al"][k] for r in rows if r["al"][k] is not None]
        tcl, nd = t_cluster(al, days)
        per_yr = 244.0 / k
        ag = 100 * statistics.mean(al) if al else None
        out[str(k)] = {
            "n": len(tot),
            "mean": round(100 * statistics.mean(tot), 3),
            "t": round(_tstat(tot) or 0, 2),
            "p10": round(100 * _q(tot, 0.10), 2),
            "p25": round(100 * _q(tot, 0.25), 2),
            "p50": round(100 * _q(tot, 0.50), 2),
            "p75": round(100 * _q(tot, 0.75), 2),
            "p90": round(100 * _q(tot, 0.90), 2),
            "win": round(100 * sum(1 for x in tot if x > 0) / len(tot), 1),
            "A_pct": round(100 * statistics.mean(al), 2) if al else None,
            "A_bp": round(1e4 * statistics.mean(al), 0) if al else None,
            "A_t": round(_tstat(al) or 0, 2) if al else None,
            "A_t_cl": round(tcl or 0, 2) if al else None,
            "n_days": nd,
            "ann_gross": round(ag * per_yr, 2) if ag is not None else None,
            "ann_cost": round(cost_bp * per_yr / 100.0, 2),
            "ann_net": (round(ag * per_yr - cost_bp * per_yr / 100.0, 2)
                        if ag is not None else None),
        }
    return out


def group_of(rows, cost, keys=None):
    """三群画像：右尾 / 中性 / 左尾。分组用毛终值，收益列同时给毛与净。"""
    keys = keys or KEYS
    def pick(lo, hi):
        return [r for r in rows if lo <= 100 * r["final"] < hi]
    groups = [
        ("右尾 >+20%", pick(G_UP, 1e9)),
        ("中性 −10~+20%", pick(G_DN, G_UP)),
        ("左尾 <−10%", pick(-1e9, G_DN)),
    ]
    out = []
    for name, rs in groups:
        if not rs:
            continue
        g = {"name": name, "n": len(rs),
             "share": round(100.0 * len(rs) / len(rows), 1),
             "final": round(100 * statistics.mean([r["final"] for r in rs]), 2),
             "peak_c": round(100 * statistics.mean([r["peak_c"] for r in rs]), 2),
             "peak_k": int(statistics.median([r["peak_k"] for r in rs])),
             "mfe": round(100 * statistics.mean([r["mfe"] for r in rs]), 2),
             "mfe_k": int(statistics.median([r["mfe_k"] for r in rs])),
             "mae": round(100 * statistics.mean([r["mae"] for r in rs]), 2),
             "mae_k": int(statistics.median([r["mae_k"] for r in rs])),
             "giveback": round(100 * statistics.mean([r["peak_c"] - r["final"] for r in rs]), 2),
             "at": {}}
        for k in keys:
            v = [r["at"][k] for r in rs]
            g["at"][str(k)] = round(100 * statistics.mean(v), 2)
        out.append(g)
    return out


def peak_hist(rows, cap=CAP):
    """峰值日分布。分组边界必须跟着 cap 走——写死到 251 时，500 日窗口里
    峰值在 252~500 日的样本会全部掉进第一个桶（首版把 68.7% 算成"1-10 日见顶"）。"""
    if cap <= 250:
        edges = (0, 10, 20, 30, 45, 60, 90, 120, 180, 251)
    else:
        edges = (0, 10, 20, 30, 45, 60, 90, 120, 180, 250, 320, 400, cap + 1)
    labels = ("1-10", "11-20", "21-30", "31-45", "46-60", "61-90",
              "91-120", "121-180", "181-250", "251-320", "321-400", "401-500")
    labels = labels[:len(edges) - 1]
    bins = [0] * len(labels)
    for r in rows:
        for i in range(len(labels)):
            if edges[i] < r["peak_k"] <= edges[i + 1]:
                bins[i] += 1
                break
        else:
            bins[0] += 1
    tot = len(rows)
    return [{"label": labels[i], "n": bins[i], "pct": round(100.0 * bins[i] / tot, 1)}
            for i in range(len(labels))]


def cond_table(rows, cap=CAP):
    """条件期望：第 k 天浮盈在某区间 → 之后到窗口末还剩多少。

    这是全场唯一能判定「止损/止盈能不能成立」的表。
    看 delta 列：负 = 该状态下继续持有还会亏，砍掉是对的；正 = 砍掉是自残。
    """
    out = []
    for k in COND_K:
        cells = []
        for lo, hi, lab in COND_BINS:
            rs = [r for r in rows if lo <= 100 * r["at"][k] < hi]
            if len(rs) < 20:
                continue
            rest = [r["at"][cap] - r["at"][k] for r in rs]
            # 剩余区间的超额：从第 k 天到窗口末的基准差
            keep = [r for r in rs
                    if r["al"][cap] is not None and r["al"][k] is not None]
            rest_a = [r["al"][cap] - r["al"][k] for r in keep]
            # 聚类 t：同一入场日的几十只票是一次冲击，不是几十个独立观测。
            # 只报逐笔 t 会让"第 20 天浮亏越多后续越差"这种结论凭空显著。
            tcl, ndays = t_cluster(rest_a, [r["ed"] for r in keep]) if rest_a \
                else (None, 0)
            cells.append({
                "bin": lab, "n": len(rs),
                "share": round(100.0 * len(rs) / len(rows), 1),
                "state": round(100 * statistics.mean([r["at"][k] for r in rs]), 2),
                "rest_ret": round(100 * statistics.mean(rest), 2),
                "rest_t": round(_tstat(rest) or 0, 2),
                "rest_alpha": round(100 * statistics.mean(rest_a), 2) if rest_a else None,
                "rest_alpha_t": round(_tstat(rest_a) or 0, 2) if rest_a else None,
                "rest_alpha_t_cl": round(tcl or 0, 2) if rest_a else None,
                "rest_alpha_ndays": ndays,
                "rest_win": round(100 * sum(1 for x in rest if x > 0) / len(rest), 1),
                "final": round(100 * statistics.mean([r["at"][cap] for r in rs]), 2),
            })
        out.append({"k": k, "cells": cells})
    return out


def run(slip_bp=51.0, comm_bp=20.0, workers=8, write=True, cap=None):
    cap = int(cap or CAP)
    keys = keys_for(cap)
    cost_bp = comm_bp + 2 * slip_bp
    per = SE.load_signals()
    jobs = []
    for c, g in per.items():
        if not g["buy"]:
            continue
        buys = sorted(g["buy"])
        jobs.append((c, buys, {d: g["beta"].get(d) for d in buys}, cap, keys))

    if workers <= 1:
        res = [work(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(work, jobs, chunksize=16))

    rows, miss = [], []
    for code, rs, err in res:
        if err:
            miss.append(code)
        rows.extend(rs)
    if not rows:
        raise SystemExit("无样本")

    # 神仙上界：完美预见每一天
    orc = [r["oracle"] for r in rows]
    orc_a = [r["oracle_a"] for r in rows if r["oracle_a"] is not None]
    orc_k = [r["oracle_k"] for r in rows]
    orc_hold = statistics.mean(orc_k)
    res_obj = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "span": [SE.SPAN_LO, SE.SPAN_HI],
        "cap": cap,
        "cost_bp": cost_bp,
        "caliber": {
            "buy": "只 1买",
            "entry": "detect_date 之后第一个交易日开盘（T+1）",
            "path": "第 k 个交易日收盘价 / 入场价 − 1（毛，未扣成本）",
            "alpha": "路径收益 − β × 等权全A 同区间收益（与 hf_sell_exit 同一套函数）",
            "cost_line": "年化净 = (244/T)×(A(T) − C)，C = %gbp 双边" % cost_bp,
            "note": "分组按毛终值；净 = 毛 − 成本，成本与持期无关（按笔收）",
        },
        "n_trades": len(rows),
        "n_codes": len(jobs),
        "n_miss_bars": len(miss),
        "curve": curve_of(rows, cost_bp, keys),
        "groups": group_of(rows, cost_bp, keys),
        "peak_hist": peak_hist(rows, cap),
        "cond": cond_table(rows, cap),
        "oracle": {
            "mean": round(100 * statistics.mean(orc), 2),
            "alpha_pct": round(100 * statistics.mean(orc_a), 2) if orc_a else None,
            "alpha_bp": round(1e4 * statistics.mean(orc_a), 0) if orc_a else None,
            "hold_mean": round(orc_hold, 1),
            "ann_gross": (round(100 * statistics.mean(orc_a) * 244.0 / orc_hold, 2)
                          if orc_a and orc_hold else None),
            "note": "完美预见的上界；任何规则都不可能超过它，只能逼近",
        },
        "giveback": {},
    }
    # 回吐：峰值收盘 − 终值
    gb = [100 * (r["peak_c"] - r["final"]) for r in rows]
    res_obj["giveback"] = {
        "mean": round(statistics.mean(gb), 2),
        "median": round(statistics.median(gb), 2),
        "pct_over_5": round(100 * sum(1 for x in gb if x > 5) / len(gb), 1),
        "pct_over_10": round(100 * sum(1 for x in gb if x > 10) / len(gb), 1),
        "pct_over_20": round(100 * sum(1 for x in gb if x > 20) / len(gb), 1),
        "pct_neg": round(100 * sum(1 for x in gb if x < 0) / len(gb), 1),
        "note": "负值=终值是全程最高点，即尾部还在创新高",
    }
    # 极端收益的集中度：右尾贡献了多少总超额
    al_final = [r["al"][cap] for r in rows if r["al"][cap] is not None]
    if al_final:
        s = sorted(al_final)
        top5 = s[int(len(s) * 0.95):]
        bot5 = s[:max(1, int(len(s) * 0.05))]
        tot = sum(s)
        res_obj["concentration"] = {
            "n": len(s),
            "total_bp": round(1e4 * tot, 0),
            "mean_bp": round(1e4 * statistics.mean(s), 0),
            "top5_pct": round(100.0 * sum(top5) / tot, 1) if tot else None,
            "bot5_pct": round(100.0 * sum(bot5) / tot, 1) if tot else None,
            "note": "top5=最好的 5% 贡献了总超额的百分之几；>100% 说明全靠右尾",
        }

    if write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res_obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    print("样本 %d 笔 / %d 票，观察窗 %d 日，成本线 %.0fbp"
          % (len(rows), len(jobs), cap, cost_bp))
    print()
    print("%5s %9s %8s %8s %9s %9s %9s %9s" % (
        "T", "笔均毛", "中位", "胜率", "A(毛%)", "毛年化", "成本", "净年化"))
    for k in keys:
        c = res_obj["curve"][str(k)]
        print("%5d %8.2f%% %8.2f%% %7.1f%% %9.2f %8.2f%% %8.2f%% %8.2f%%" % (
            k, c["mean"], c["p50"], c["win"], c["A_pct"] or 0,
            c["ann_gross"] or 0, c["ann_cost"], c["ann_net"] or 0))
    print()
    print("三群画像：")
    for g in res_obj["groups"]:
        print("  %-14s n=%5d (%4.1f%%) 终值 %+7.2f%%  峰值 %+7.2f%% @%3d日  "
              "MFE %+7.2f%%  MAE %+7.2f%%  回吐 %+6.2fpp"
              % (g["name"], g["n"], g["share"], g["final"], g["peak_c"],
                 g["peak_k"], g["mfe"], g["mae"], g["giveback"]))
    print()
    print("峰值日分布：")
    print("  " + "  ".join("%s:%.1f%%" % (x["label"], x["pct"])
                           for x in res_obj["peak_hist"]))
    print()
    print("条件期望（第 k 天状态 → 之后到窗口末 %d 日）：" % cap)
    for t in res_obj["cond"]:
        print("  k=%d" % t["k"])
        for c in t["cells"]:
            print("    %-10s n=%5d  当前 %+6.2f%%  后续 %+6.2f%% (t=%5.2f)  "
                  "后续超额 %+6.2f%% (t=%5.2f / 聚类 %5.2f)  后涨率 %4.1f%%  终值 %+6.2f%%"
                  % (c["bin"], c["n"], c["state"], c["rest_ret"], c["rest_t"],
                     c["rest_alpha"] or 0, c["rest_alpha_t"] or 0,
                     c["rest_alpha_t_cl"] or 0, c["rest_win"], c["final"]))
    print()
    o = res_obj["oracle"]
    print("神仙上界：笔均 %+.2f%%，超额 %+.2f%%（%.0fbp），平均持 %.0f 日，毛年化 %.2f%%"
          % (o["mean"], o["alpha_pct"] or 0, o["alpha_bp"] or 0,
             o["hold_mean"], o["ann_gross"] or 0))
    print("回吐：均值 %.2fpp，>10pp 占 %.1f%%，>20pp 占 %.1f%%"
          % (res_obj["giveback"]["mean"], res_obj["giveback"]["pct_over_10"],
             res_obj["giveback"]["pct_over_20"]))
    return res_obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=51.0, help="单边滑点 bp（默认 51）")
    ap.add_argument("--comm", type=float, default=20.0, help="佣金双边 bp")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cap", type=int, default=250, help="观察窗（交易日），可拉到 500")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = os.path.join(ROOT, "portal", a.out)
    run(slip_bp=a.slip, comm_bp=a.comm, workers=a.workers, write=not a.no_write,
        cap=a.cap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
