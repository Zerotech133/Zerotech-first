# -*- coding: utf-8 -*-
"""hf_exit_lab：出场规则对照（第十九轮，出场规则第二步）。

## 要回答的问题

`hf_path` 摊开路径后会看到：alpha 的累积不是线性的，毛超额 A(T) 随持期
先升后降再升，而成本按笔收、与持期无关。于是年化净

    ann_net = (244 / mean(T)) × (mean(A_毛) − C)      C = 122bp

能不能大于零，只取决于**单笔毛超额有没有越过 122bp**，跟持期长短无关；
持期只决定把这个差额放大几倍。

那么出场规则唯一该干的事，是**在没有坏掉的样本上把 A 抬起来**：
砍掉左尾（省下后续下跌）、留住右尾（吃满后续上涨）。两件事都必须
在被触发样本上验证「触发之后那段时间到底怎么走」。

本脚本把 12 条规则放在**同一批 6,903 笔**上跑，逐笔给：

    exit_k   实际持了几天
    A_毛     这笔最终吃到的毛超额（bp）
    post     触发之后到 250 日还会走多少（正=砍早了，负=砍对了）

`post` 是判据的核心：**成本已经付了，砍掉只改变后面那段归不归你。**
post 为正说明这条规则在自残，不管净值好不好看。

## 规则为什么是这几条

    结构止损   缠论本体：收盘跌破中枢上沿 ZG / 最近笔低点即失效
    绝对止损   通用风控，作为"结构位算不出来"时的兜底
    追踪止损   让利润奔跑的常见形态（峰值回撤 X%）
    放飞       浮盈超过阈值后关掉所有止损，只留时间上限
    时间止损   缠论语义「买入后应迅速脱离成本区」
    卖点       本体信号（1卖/2卖/3卖 取最早），上一轮已证伪，留作对照
    组合       结构止损 + 追踪 / 结构 + 放飞

出场价一律**触发日次日开盘**：收盘才看得到信号，同刻成交是未来函数。
固定持期例外（与 hf_sell_exit 一致，第 N 日收盘），否则 hold250 无法复现。

用法::

    python -X utf8 tools/research/hf_exit_lab.py
    python -X utf8 tools/research/hf_exit_lab.py --slip 20
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_book as H            # noqa: E402
import hf_sell_exit as SE      # noqa: E402
import hf_bench as HB          # noqa: E402
import hf_cost as CO           # noqa: E402
import hf_path as HP           # noqa: E402  基准前缀积与 hf_path 共用一套

ROOT = H.ROOT
OUT = os.path.join(ROOT, "portal", "hf_exit_lab.json")

CAP = 250
HOLD_REFS = (20, 60, 120, 250)

# 规则表：name → cfg
#   pnl   绝对止损（相对入场价，负数为亏损幅度）
#   trail 追踪止损（相对持仓期收盘峰值回撤）
#   zg    结构止损（收盘破结构位）
#   fly   浮盈达到该值后关掉上面三条，只留时间上限
#   sell  持到第一个卖点（1卖/2卖/3卖）
#   tk    k 日时间止损，触发条件 tp（该日收益 < tp）
#   cap   时间上限
RULES = [
    ("hold250",      dict(cap=250)),
    ("hold120",      dict(cap=120)),
    ("hold60",       dict(cap=60)),
    ("hold20",       dict(cap=20)),
    ("sell_any",     dict(sell=True, cap=250)),
    ("zg",           dict(zg=True, cap=250)),
    ("zg60",         dict(zg=True, cap=60)),
    ("pnl8",         dict(pnl=0.08, cap=250)),
    ("pnl12",        dict(pnl=0.12, cap=250)),
    ("trail20",      dict(trail=0.20, cap=250)),
    ("trail30",      dict(trail=0.30, cap=250)),
    ("zg_trail30",   dict(zg=True, trail=0.30, cap=250)),
    ("zg_fly20",     dict(zg=True, fly=0.20, cap=250)),
    ("pnl12_fly20",  dict(pnl=0.12, fly=0.20, cap=250)),
    ("time20",       dict(tk=20, tp=0.0, cap=250)),
    ("time60",       dict(tk=60, tp=0.0, cap=250)),
    ("zg_time60",    dict(zg=True, tk=60, tp=0.0, cap=250)),
    ("lock20",       dict(lock=0.20, lock_to=0.05, cap=250)),
    ("hold_win",     dict(lock=0.20, lock_to=0.0, cap=250)),
    # 结构位贴太近是 zg 的原罪：入场价常在中枢上沿附近，止损距离只有 2~3%，
    # 一点噪声就打掉。加最小距离约束，把止损推到 max(结构位, 入场价−X%)。
    ("zg_w8",        dict(zg=True, zg_min=0.08, cap=250)),
    ("zg_w10",       dict(zg=True, zg_min=0.10, cap=250)),
    ("zg_w10_fly20", dict(zg=True, zg_min=0.10, fly=0.20, cap=250)),
]


def _first_gt(dates, d):
    j = bisect.bisect_right(dates, d)
    return j if j < len(dates) else None


def sim(path, get_open_ret, sl_r, sell_k, cfg, cap):
    """单笔规则模拟 → (exit_k, exit_ret, fired_reason)。

    path[k] 是第 k 日收盘相对入场价的毛收益；触发后次日开盘成交，
    所以出场价用 get_open_ret(k+1)，k+1 越界则退到 cap 收盘。
    """
    peak = path[0]
    flew = False
    armed = False          # lock：是否已触发过"曾浮盈达标"
    cpnl = cfg.get("pnl")
    ctr = cfg.get("trail")
    czg = cfg.get("zg")
    fly = cfg.get("fly")
    sell = cfg.get("sell")
    tk = cfg.get("tk")
    tp = cfg.get("tp", 0.0)
    lock = cfg.get("lock")
    lock_to = cfg.get("lock_to", 0.0)

    for k in range(1, cap + 1):
        r = path[k]
        if r > peak:
            peak = r
        if fly is not None and r >= fly:
            flew = True
        if lock is not None and r >= lock:
            armed = True
        why = None
        if not flew:
            if czg and sl_r is not None and r < sl_r:
                why = "zg"
            elif cpnl is not None and r <= -cpnl:
                why = "pnl"
            elif ctr is not None and r <= peak - ctr:
                why = "trail"
        if why is None and lock is not None and armed and r <= lock_to:
            why = "lock"
        if why is None and tk is not None and k == tk and r < tp:
            why = "time"
        if why is None and sell and sell_k is not None and k >= sell_k:
            why = "sell"
        if why is not None:
            # 出场日 = 次日（成交在次日开盘）。返回的 exit_k 必须与成交价同日，
            # 否则基准窗口会比收益窗口少一天——收益含次日开盘跳空、基准不含，
            # 每一笔都会凭空多半个月的漂移。首版写的就是 `return k`（触发日）。
            if k + 1 > cap:
                return cap, path[cap], why
            return k + 1, get_open_ret(k + 1), why
    return cap, path[cap], "cap"


def work(job):
    code, buys, sells, betas, cap = job
    b = H.load_bars(code, "20230901")
    if b is None:
        return code, [], "no_bars"
    dates = b.dates
    n = len(dates)
    sell_d = [d for d, _ in sells]
    out = []

    import chan_struct as CS

    for d0 in sorted(buys):
        ie = _first_gt(dates, d0)
        if ie is None or ie + cap >= n:
            continue
        px_in = b.px_at_k(ie, ie, "o")
        if not px_in or px_in <= 0:
            continue
        ed = dates[ie]
        beta = betas.get(d0)

        path = [0.0] * (cap + 1)
        for k in range(1, cap + 1):
            px = b.px_at_k(ie + k, ie, "c")
            path[k] = (px / px_in - 1.0) if px and px > 0 else path[k - 1]

        def get_open_ret(k, _ie=ie, _px=px_in):
            if k > cap:
                return path[cap]
            px = b.px_at_k(_ie + k, _ie, "o")
            return (px / _px - 1.0) if px and px > 0 else path[k]

        # 结构位：信号在 d0 收盘确认，结构只能用 d0 及之前 → before_d 传建仓日
        st = CS.struct_level(code, ed, px_in)
        sl_r = (st["sl"] / px_in - 1.0) if (st and st.get("sl")) else None

        j = bisect.bisect_right(sell_d, d0)
        sell_k = None
        if j < len(sell_d):
            si = b.pos.get(sell_d[j])
            if si is not None and si > ie:
                sell_k = si - ie

        rec = {"code": code, "d0": d0, "ed": ed, "beta": beta,
               "sl_r": sl_r, "sl_kind": (st or {}).get("kind"),
               "sell_k": sell_k, "final": path[cap], "rules": {}}
        # 同窗口径的分母：整段 250 日的基准项，所有规则共用。
        # 为什么必须共用：提前出场会让"测量区间"缩短，若沿用出场日的基准，
        # 那么"在下跌途中砍掉"会凭空得到一个正超额——那不是能力，是敞口。
        br250 = HP.bench_iv(ed, dates[ie + cap], beta)
        for name, cfg in RULES:
            c = cfg.get("cap", cap)
            # 最小距离：结构位不能贴着成本，否则任何噪声都触发
            s_use = sl_r
            if cfg.get("zg") and sl_r is not None and cfg.get("zg_min"):
                s_use = min(sl_r, -float(cfg["zg_min"]))
            ek, er, why = sim(path, get_open_ret, s_use, sell_k, cfg, c)
            br = HP.bench_iv(ed, dates[ie + ek], beta) if ek <= cap else None
            rec["rules"][name] = {
                "k": ek, "r": er, "why": why,
                "a": (er - br) if br is not None else None,
                # a_win：出场后现金，基准照跑满 250 日（严格口径）
                "a_win": (er - br250) if br250 is not None else None,
                "br_exit": br,
            }
        out.append(rec)
    return code, out, None


# ---------------------------------------------------------------- 聚合
def _tstat(xs):
    n = len(xs)
    if n < 3:
        return None
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    return (m / (sd / math.sqrt(n))) if sd else None


# 聚类 t 与 hf_path 共用一份实现：两处各写一份就是两个口径，
# 而这个函数恰恰是用来否定"t 很高"的，写歪了会把结论翻回去。
t_cluster = HP.t_cluster


def summ(rows, name, cost_bp):
    """ratio-of-means 年化：244 / mean(T) × (mean(A) − C)。

    为什么不是逐笔年化再平均：一笔 5 天和一笔 250 天在一年里出现的次数
    差 50 倍，逐笔平均等于把长持有当成和短持有一样常见。资金顺序滚动时，
    长期增长率就是 Σr/ΣT，即 E[r]/E[T]——比值口径。

    **单位必须是 bp**：A 和 C 都要用 bp 比才能判过线（1.96% vs 122bp
    直接相减会得出"远小于成本"的错误结论）。年化那一步再 /100 转回百分点。
    """
    a = [r["rules"][name]["a"] for r in rows if r["rules"][name]["a"] is not None]
    ad = [r["ed"] for r in rows if r["rules"][name]["a"] is not None]
    aw = [r["rules"][name]["a_win"] for r in rows if r["rules"][name]["a_win"] is not None]
    k = [r["rules"][name]["k"] for r in rows]
    if not a:
        return None
    tm = statistics.mean(k)
    ag_bp = 1e4 * statistics.mean(a)
    aw_bp = 1e4 * statistics.mean(aw) if aw else None
    fired = [r for r in rows if r["rules"][name]["why"] != "cap"]
    kept = [r for r in rows if r["rules"][name]["why"] == "cap"]
    # 触发之后到 250 日还会走多少：正=砍早了，负=砍对了
    post = [r["final"] - r["rules"][name]["r"] for r in fired]
    # 触发时那段窗口的市场表现：很负说明「砍在下跌途中」——α 改善只是敞口缩短
    bfx = [r["rules"][name]["br_exit"] for r in fired
           if r["rules"][name]["br_exit"] is not None]
    a_f = [r["rules"][name]["a"] for r in fired if r["rules"][name]["a"] is not None]
    a_k = [r["rules"][name]["a"] for r in kept if r["rules"][name]["a"] is not None]
    tcl, nday = t_cluster(a, ad)
    return {
        "n": len(a),
        "n_days": nday,
        "hold_mean": round(tm, 1),
        "hold_med": int(statistics.median(k)),
        "A_bp": round(ag_bp, 0),
        "A_pct": round(ag_bp / 100.0, 2),
        "A_t": round(_tstat(a) or 0, 2),
        "A_t_cl": round(tcl or 0, 2),
        "win": round(100 * sum(1 for x in a if x > 0) / len(a), 1),
        "median_bp": round(1e4 * statistics.median(a), 0),
        "fire_n": len(fired),
        "fire_pct": round(100.0 * len(fired) / len(rows), 1),
        "A_fired_bp": round(1e4 * statistics.mean(a_f), 0) if a_f else None,
        "A_kept_bp": round(1e4 * statistics.mean(a_k), 0) if a_k else None,
        "exitr_fired_pct": round(100 * statistics.mean(
            [r["rules"][name]["r"] for r in fired]), 1) if fired else None,
        "final_fired_pct": round(100 * statistics.mean(
            [r["final"] for r in fired]), 1) if fired else None,
        "final_kept_pct": round(100 * statistics.mean(
            [r["final"] for r in kept]), 1) if kept else None,
        "post_bp": round(1e4 * statistics.mean(post), 0) if post else None,
        "post_t": round(_tstat(post) or 0, 2) if post else None,
        "br_exit_pct": round(100 * statistics.mean(bfx), 1) if bfx else None,
        "A_win_bp": round(aw_bp, 0) if aw_bp is not None else None,
        "ann_gross": round(ag_bp * 244.0 / tm / 100.0, 2) if tm else None,
        "ann_cost": round(cost_bp * 244.0 / tm / 100.0, 2) if tm else None,
        "ann_net": (round((ag_bp - cost_bp) * 244.0 / tm / 100.0, 2) if tm else None),
        "ann_cash": (round((aw_bp - cost_bp) * 244.0 / CAP / 100.0, 2)
                     if aw_bp is not None else None),
        "pass": bool((ag_bp - cost_bp) > 0),
        "pass_cash": bool(aw_bp is not None and (aw_bp - cost_bp) > 0),
        # 盈亏平衡成本：A_bp 就是这个规则能承受的单笔双边成本上限
        "be_cost_bp": round(ag_bp, 0),
    }


def run(slip_bp=51.0, comm_bp=20.0, workers=8, write=True):
    cost_bp = CO.total_bp(comm_bp, slip_bp)
    per = SE.load_signals()
    jobs = []
    for c, g in per.items():
        if not g["buy"]:
            continue
        jobs.append((c, sorted(g["buy"]), g["sell"],
                     {d: g["beta"].get(d) for d in g["buy"]}, CAP))

    if workers <= 1:
        res = [work(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(work, jobs, chunksize=8))

    rows, miss = [], []
    for code, rs, err in res:
        if err:
            miss.append(code)
        rows.extend(rs)
    if not rows:
        raise SystemExit("无样本")

    tab = {}
    for name, _ in RULES:
        tab[name] = summ(rows, name, cost_bp)

    # 配对差：同一笔，规则 vs hold250（笔均毛超额差 bp）
    base = [r["rules"]["hold250"]["a"] for r in rows]
    for name, _ in RULES:
        if name == "hold250":
            continue
        d = [r["rules"][name]["a"] - b for r, b in zip(rows, base)
             if r["rules"][name]["a"] is not None and b is not None]
        dd = [r["ed"] for r, b in zip(rows, base)
              if r["rules"][name]["a"] is not None and b is not None]
        tab[name]["diff_bp"] = round(1e4 * statistics.mean(d), 0) if d else None
        tab[name]["diff_t"] = round(_tstat(d) or 0, 2) if d else None
        tab[name]["diff_t_cl"] = round((t_cluster(d, dd)[0] or 0), 2) if d else None

    # 结构位可用率与距离分布：距离太近是 zg 规则失效的直接原因
    has = sum(1 for r in rows if r["sl_r"] is not None)
    dist = sorted(-100 * r["sl_r"] for r in rows if r["sl_r"] is not None)
    tight = sum(1 for x in dist if x < 3)

    def dq(p):
        return round(dist[min(len(dist) - 1, int(len(dist) * p))], 2) if dist else None

    # 成本阶梯：A 与 T 都与成本无关（路径不变），所以可以直接代数推出各档年化。
    # 这条比"再跑一遍"更硬——它证明成本是唯一的自由变量，不是拟合出来的。
    ladder = {}
    for lv in CO.LEVELS:
        c = CO.total_bp(lv["comm_bp"], lv["slip_bp"])
        row = {}
        for name in tab:
            s = tab[name]
            if not s:
                continue
            row[name] = round((s["A_bp"] - c) * 244.0 / s["hold_mean"] / 100.0, 2)
        ladder[lv["k"]] = {"label": lv["label"], "cost_bp": round(c, 1), "ann": row}

    # 按年拆：只有跨年都成立的改善才算数（单年显著多半是那年的行情）
    by_year = {}
    years = sorted({r["ed"][:4] for r in rows})
    for y in years:
        sub = [r for r in rows if r["ed"][:4] == y]
        cell = {}
        for name in tab:
            d = [r["rules"][name]["a"] - r["rules"]["hold250"]["a"] for r in sub
                 if r["rules"][name]["a"] is not None
                 and r["rules"]["hold250"]["a"] is not None]
            dd = [r["ed"] for r in sub
                  if r["rules"][name]["a"] is not None
                  and r["rules"]["hold250"]["a"] is not None]
            if len(d) < 30:
                continue
            cell[name] = {"n": len(d), "diff_bp": round(1e4 * statistics.mean(d), 0),
                          "t": round(_tstat(d) or 0, 2),
                          "t_cl": round(t_cluster(d, dd)[0] or 0, 2)}
        by_year[y] = cell

    obj = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "span": [SE.SPAN_LO, SE.SPAN_HI],
        "cap": CAP,
        "cost_bp": cost_bp,
        "cost_desc": CO.describe(comm_bp, slip_bp),
        "caliber": {
            "buy": "只 1买",
            "entry": "T+1 开盘",
            "exit_rule": "触发日次日开盘；固定持期例外（第 N 日收盘，与 hf_sell_exit 一致）",
            "alpha": "毛收益 − β × 等权全A 同区间（与 hf_sell_exit 同一函数）",
            "ann_net": "（暴露归一）(244/mean持期)×(A_bp−C)：假设资金立即复投，所有出场都显得更好",
            "ann_cash": "（同窗空仓）(244/250)×(A_win_bp−C)：A_win 用满 250 日的基准，出场后计现金",
            "why_two": "两者是上下界。A（暴露归一）对提前出场有 stopping-time 偏差："
                       "砍在下跌途中，测量区间停在低点，β 项缩水 → 凭空出现正超额。"
                       "真实值取决于资金复用率，而复用一次要再付 122bp、只带回 196bp，"
                       "净 74bp/250 日 ≈ 0.29bp/日，所以同窗口径更接近真相。",
            "pass": "笔均毛超额 > 成本线；pass_cash 用 A_win 判",
        },
        "n_trades": len(rows),
        "n_days": len({r["ed"] for r in rows}),
        "n_codes": len(jobs),
        "n_miss_bars": len(miss),
        "zg_usable": {"n": has, "pct": round(100.0 * has / len(rows), 1),
                      "tight_3pct": tight,
                      "dist_med": dq(0.5), "dist_p25": dq(0.25), "dist_p75": dq(0.75),
                      "note": "结构位距离（入场价到止损位）的分布；中位若只有 2~3%，"
                              "说明止损贴在成本上，zg 会被日常噪声打掉"},
        "table": tab,
        "ladder": ladder,
        "by_year": by_year,
        "best": max((k for k in tab if tab[k]), key=lambda k: tab[k]["ann_net"] or -999),
        "best_cash": max((k for k in tab if tab[k] and tab[k]["ann_cash"] is not None),
                         key=lambda k: tab[k]["ann_cash"]),
    }
    if write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    print("样本 %d 笔 / %d 票，成本线 %.0fbp（%s）" % (len(rows), len(jobs), cost_bp,
                                                CO.describe(comm_bp, slip_bp)))
    print("结构位可用 %d 笔（%.1f%%），距离中位 %.2f%%（p25 %.2f / p75 %.2f），<3%% 占 %.0f%%"
          % (has, 100.0 * has / len(rows), dq(0.5) or 0, dq(0.25) or 0,
             dq(0.75) or 0, 100.0 * tight / max(1, len(dist))))
    print()
    print("%-12s %6s %7s %7s %6s %7s %8s %8s %8s" % (
        "规则", "持期", "A(毛bp)", "t笔", "t日", "A同窗", "年化净", "年化空仓", "过线"))
    for name, _ in RULES:
        s = tab[name]
        if not s:
            continue
        print("%-12s %6.1f %7.0f %7.2f %6.2f %7s %8.2f %8.2f %6s" % (
            name, s["hold_mean"], s["A_bp"], s["A_t"], s["A_t_cl"],
            ("%.0f" % s["A_win_bp"]) if s["A_win_bp"] is not None else "-",
            s["ann_net"] or 0, s["ann_cash"] or 0,
            "✓" if s["pass"] else "✗"))
    print()
    print("配对差（同一笔，规则 vs hold250，笔均毛超额 bp）：")
    print("%-14s %8s %8s %8s %8s %8s %8s" % (
        "规则", "配对差", "t(笔)", "t(日聚类)", "触发率", "触发后还走", "同窗差"))
    for name, _ in RULES:
        s = tab[name]
        if not s or name == "hold250":
            continue
        print("  %-12s %8.0f %8.2f %8.2f %8.1f%% %10.0f %8.0f"
              % (name, s.get("diff_bp") or 0, s.get("diff_t") or 0,
                 s.get("diff_t_cl") or 0, s["fire_pct"], s["post_bp"] or 0,
                 (s["A_win_bp"] or 0) - (tab["hold250"]["A_win_bp"] or 0)))
    print()
    print("分组核对（触发组 vs 未触发组）：")
    print("%-12s %6s %9s %9s %9s %9s %9s" % (
        "规则", "触发%", "触发组A", "未触发A", "触发时退", "触发组终值", "未触发终值"))
    for name, _ in RULES:
        s = tab[name]
        if not s or s["fire_pct"] < 1:
            continue
        print("%-12s %6.1f %9s %9s %9s %11s %12s" % (
            name, s["fire_pct"],
            ("%.0f" % s["A_fired_bp"]) if s["A_fired_bp"] is not None else "-",
            ("%.0f" % s["A_kept_bp"]) if s["A_kept_bp"] is not None else "-",
            ("%.1f" % s["exitr_fired_pct"]) if s["exitr_fired_pct"] is not None else "-",
            ("%.1f" % s["final_fired_pct"]) if s["final_fired_pct"] is not None else "-",
            ("%.1f" % s["final_kept_pct"]) if s["final_kept_pct"] is not None else "-"))
    print()
    print("成本阶梯（年化净，A 与 T 与成本无关，纯代数）：")
    keys = [lv["k"] for lv in CO.LEVELS]
    print("%-12s" % "规则" + "".join("%14s" % ("%s %.0fbp" % (ladder[k]["label"], ladder[k]["cost_bp"]))
                                     for k in keys))
    for name, _ in RULES:
        if not tab[name]:
            continue
        print("%-12s" % name + "".join("%13.2f%%" % ladder[k]["ann"].get(name, 0) for k in keys))
    print()
    print("按年拆（配对差 bp / t）：")
    print("%-12s" % "规则" + "".join("%18s" % y for y in years))
    for name, _ in RULES:
        if name == "hold250" or not tab[name]:
            continue
        line = "%-12s" % name
        for y in years:
            c = by_year.get(y, {}).get(name)
            line += ("%18s" % ("%+.0f / %+.2f" % (c["diff_bp"], c["t"]))) if c else "%18s" % "-"
        print(line)
    print()
    print("最好（暴露归一）：%s 年化 %.2f%% ｜ 最好（同窗空仓）：%s 年化 %.2f%%"
          % (obj["best"], tab[obj["best"]]["ann_net"] or 0,
             obj["best_cash"], tab[obj["best_cash"]]["ann_cash"] or 0))
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=51.0)
    ap.add_argument("--comm", type=float, default=20.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = os.path.join(ROOT, "portal", a.out)
    run(slip_bp=a.slip, comm_bp=a.comm, workers=a.workers, write=not a.no_write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
