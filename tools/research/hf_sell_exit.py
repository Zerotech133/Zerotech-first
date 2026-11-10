# -*- coding: utf-8 -*-
"""hf_sell_exit：持到卖点 vs 固定持期（第十六轮 G1 续）

**为什么必须做这一步**

`hf_sell_density` 量出来：1买 之后第一个卖点的中位间隔是 **104 个交易日**
（p25 55 / p75 191 / p90 317），而 20 个交易日内等到卖点的只占 **4.6%**。

那么 `chanlun_exit_policy` 那个"卖点离场 vs 满期 20 日"的对照（差别不到
0.1pp）根本没测到卖点——它测的是"20 天窗口里那 4.6% 早退的样本"。用 20 天
的尺子量 5 个月的东西，当然量出"没差别"。

要回答的是：**如果真按纯正缠论持到卖点，会怎样。**

**口径**

    样本      只 1买，detect_date ∈ [2024-01-02, 2026-11-04]
    入场      T+1 开盘（detect_date 之后第一个交易日）
    出场      hold·N → 第 N 个交易日收盘
              卖点   → 同标的后第一个卖点确认日的 T+1 开盘
              无卖点 → 窗口末收盘（右删失，单独统计）
    成本      买入 0.075% + 卖出 0.125%（佣金+印花），滑点单列
    超额      − 同期 000985.CSI 收益（同日历区间）

**两条对照必须同时看**

    ret   绝对收益：持得久 = β 暴露久，A 股三年是涨的，绝对收益会虚高
    alpha 超额：扣掉指数后还剩多少，这才是「缠论这个结构赚不赚钱」

用法::

    python -X utf8 tools/research/hf_sell_exit.py
    python -X utf8 tools/research/hf_sell_exit.py --slip 20     # 加 20bp 单边滑点
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_book as H  # noqa: E402

ROOT = H.ROOT
JSONL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
IDX = os.path.join(ROOT, "quant_backtest", "data", "tdx", "ts_index_daily")
OUT = os.path.join(ROOT, "portal", "hf_sell_exit.json")

BUY = "1买"
SELL = ("1卖", "2卖", "3卖")
HOLDS = (5, 10, 20, 30, 60, 120, 250)

BUY_COST = 0.00075
SELL_COST = 0.00125

SPAN_LO = "20240102"
SPAN_HI = "20261104"


def _n8(d):
    return str(d).replace("-", "").replace("/", "")[:8]


# ---------------------------------------------------------------- 日历/指数
_CAL = None


def calendar():
    """交易日历 + 指数收益序列（000985.CSI）。"""
    global _CAL
    if _CAL is not None:
        return _CAL
    import pandas as pd

    rows = []
    for p in sorted(glob.glob(os.path.join(IDX, "part_*.parquet"))):
        df = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        sub = df[df["ts_code"] == "000985.CSI"]
        if len(sub):
            rows.append(sub[["trade_date", "close"]])
    if not rows:
        raise SystemExit("指数 000985.CSI 无数据")
    d = pd.concat(rows).drop_duplicates("trade_date").sort_values("trade_date")
    dates = [_n8(x) for x in d["trade_date"].tolist()]
    close = [float(x) for x in d["close"].tolist()]
    idx = {dt: i for i, dt in enumerate(dates)}
    _CAL = (dates, close, idx)
    return _CAL


_EW = None


def bench_ew():
    """等权全 A 基准：与 chanlun_alpha.bench_ew() 同源缓存，口径必须一致。

    用 000985.CSI（市值加权）会漏掉小盘因子——池内绝大多数是中小盘，
    拿市值加权当基准，等于把「小盘涨得比大盘多」算成缠论的 alpha。
    """
    global _EW
    if _EW is None:
        d = json.load(open(os.path.join(ROOT, "quant_backtest", "data",
                                        "chanlun_bench_ew.json"), encoding="utf-8"))
        _EW = (d["dates"], d["ret"], {x: i for i, x in enumerate(d["dates"])})
    return _EW


def _pos_le(dates, d):
    return bisect.bisect_right(dates, d) - 1


def bench_window_ret(d0, d1):
    """基准在 (d0, d1] 的累计收益（与 raw 的收→收区间对齐）。"""
    dates, ret, idx = bench_ew()
    i = idx.get(d0)
    j = idx.get(d1)
    if i is None or j is None or j <= i:
        return None
    acc = 1.0
    for k in range(i + 1, j + 1):
        acc *= (1.0 + ret[k])
    return acc - 1.0


BENCH = "ew"   # ew = 等权全A + 逐笔β（默认）；csi = 市值加权 000985.CSI + β=1（旧口径）


def csi_window_ret(d0, d1):
    """市值加权基准：000985.CSI 收盘比。仅用于对照旧口径，不作为主判据。"""
    dates, close, idx = calendar()
    i0 = idx.get(d0)
    i1 = idx.get(d1)
    if i0 is None or i1 is None or close[i0] <= 0:
        return None
    return close[i1] / close[i0] - 1.0


def idx_ret(d0, d1, beta=1.0):
    """基准项。ew 口径 = β × 等权全A 区间收益；csi 口径 = 1.0 × 全指区间收益。

    csi 口径刻意不乘 β：那是 hf_book 原来的写法（alpha = 净值 − 指数涨幅），
    要复现旧结论就必须连这个缺陷一起复现，否则对照不成立。
    """
    if BENCH == "csi":
        br = csi_window_ret(d0, d1)
        return br
    br = bench_window_ret(d0, d1)
    if br is None:
        return None
    return (beta if beta else 1.0) * br


# ---------------------------------------------------------------- 信号
def load_signals():
    """返回 {code: {'buy': [d0...], 'sell': [(d,type)...]}}，日期全 8 位。"""
    per = defaultdict(lambda: {"buy": set(), "sell": [], "beta": {}})
    for line in open(JSONL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        t = r.get("type")
        c = r.get("code")
        d = r.get("detect_date")
        if not c or not d:
            continue
        d = _n8(d)
        if not (SPAN_LO <= d <= SPAN_HI):
            continue
        if t == BUY:
            per[c]["buy"].add(d)
            b = r.get("beta")
            per[c]["beta"][d] = float(b) if isinstance(b, (int, float)) else None
        elif t in SELL:
            per[c]["sell"].append((d, t))
    for c in per:
        per[c]["sell"].sort()
    return per


# ---------------------------------------------------------------- 单票计算
def _first_gt(dates, d):
    """strictly > d 的第一个下标。"""
    j = bisect.bisect_right(dates, d)
    return j if j < len(dates) else None


def work(args):
    code, buys, sells, slip_bp, cap, betas, bench = args
    # 子进程是重新 import 的模块，run() 里改的全局变量传不过来，必须在 worker 里设
    global BENCH
    BENCH = bench
    b = H.load_bars(code, "20230901")
    if b is None:
        return code, [], "no_bars"
    dates = b.dates
    sell_d = [d for d, _ in sells]
    sell1_d = [d for d, t in sells if t == "1卖"]
    slip = slip_bp / 1e4
    out = []
    for d0 in sorted(buys):
        ie = _first_gt(dates, d0)
        if ie is None:
            continue
        # 统一观察窗：cap 设置时，所有口径都只保留「入场后能看满 cap 个交易日」
        # 的样本。这样没有任何右删失，卖点口径与固定持期口径是同一批票，
        # 对比才无偏。不设 cap 时退化为「有卖点就持到卖点、没有就末日平仓」。
        if cap and ie + cap >= len(dates):
            continue
        # 入场价：T+1 开盘（px_at_k 在 ei == i 时就是原始开盘价）
        px_in = b.px_at_k(ie, ie, "o")
        if not px_in or px_in <= 0:
            continue
        base = {
            "code": code, "d0": d0, "entry_d": dates[ie],
            "entry_px": round(px_in, 4),
            "slip_bp": slip_bp,
        }
        # 固定持期
        for h in HOLDS:
            ix = ie + h
            if ix >= len(dates):
                continue
            px_out = b.px_at_k(ix, ie, "c")
            if not px_out or px_out <= 0:
                continue
            rec = dict(base, mode="hold%d" % h, exit_d=dates[ix],
                       hold=ix - ie, censored=False)
            rec["ret"] = px_out / px_in - 1.0
            rec["idx"] = idx_ret(dates[ie], dates[ix], betas.get(d0))
            out.append(rec)
        # 持到卖点：任一 / 仅 1卖
        for label, src in (("sell_any", sell_d), ("sell_1s", sell1_d)):
            j = bisect.bisect_right(src, d0)
            if j >= len(src):
                # 窗口内没有卖点：cap 口径走满 cap，否则走到末日（右删失）
                ix = (ie + cap) if cap else (len(dates) - 1)
                if ix <= ie:
                    continue
                px_out = b.px_at_k(ix, ie, "c")
                if not px_out or px_out <= 0:
                    continue
                rec = dict(base, mode=label, exit_d=dates[ix],
                           hold=ix - ie, censored=True)
                rec["ret"] = px_out / px_in - 1.0
                rec["idx"] = idx_ret(dates[ie], dates[ix], betas.get(d0))
                out.append(rec)
            else:
                d1 = src[j]
                ix = _first_gt(dates, d1)
                cens = False
                if ix is None:
                    ix = (ie + cap) if cap else (len(dates) - 1)
                    cens = True
                elif cap and ix > ie + cap:
                    # 卖点来得比 cap 还晚：按 cap 平仓，算「窗内未触发」
                    ix = ie + cap
                    cens = True
                if ix <= ie:
                    continue
                px_out = b.px_at_k(ix, ie, "o")  # T+1 开盘出场
                if not px_out or px_out <= 0:
                    continue
                rec = dict(base, mode=label, exit_d=dates[ix],
                           hold=ix - ie, censored=cens)
                rec["ret"] = px_out / px_in - 1.0
                rec["idx"] = idx_ret(dates[ie], dates[ix], betas.get(d0))
                out.append(rec)
    return code, out, None


# ---------------------------------------------------------------- 聚合
def _tstat(xs):
    n = len(xs)
    if n < 3:
        return None
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) if n else 0
    if sd == 0:
        return None
    return m / (sd / math.sqrt(n))


def _summ(rows, slip):
    """笔级 → 年化。**成本只扣一次**。

    2026-11-08 修正：旧版把 `rets` 先扣了成本，`ann_alpha` 自然已是净值，
    却又写 `ann_net = ann_alpha - ann_cost`，成本被扣了两遍。后果是
    hold60（真值 +0.34%）显示成 −4.62%、hold250（真值 +0.72%）显示成 −0.47%，
    「无一档为正」这个结论是这么来的。

    现在把三个数分开报，互不覆盖：
        ann_gross  毛超额年化（未扣成本）
        ann_cost   年化成本（换手 × 双边 bp）
        ann_net    净超额年化 = ann_gross − ann_cost（这才是能看的那个）
    `ann_alpha` 保持旧语义（净），只为不打断并行 session 的引用；
    新代码一律用 ann_net。
    """
    if not rows:
        return None
    gross = [r["ret"] for r in rows]
    cost = BUY_COST + SELL_COST + 2 * slip
    rets = [x - cost for x in gross]
    g_alps = [x - r["idx"] for x, r in zip(gross, rows) if r["idx"] is not None]
    n_alps = [x - r["idx"] for x, r in zip(rets, rows) if r["idx"] is not None]
    holds = [r["hold"] for r in rows]
    hm = statistics.mean(holds) if holds else 0
    # 单笔年化：笔均 × 每年能滚几轮。持期不同笔均必然不同，只有年化可比。
    # 假设资金始终满仓单笔循环（不考虑同时持仓数、不考虑资金闲置），
    # 这是「这笔钱只做这一笔、做完立刻做下一笔」的口径。
    per_yr = 244.0 / hm if hm else None
    ann_ret = 100 * statistics.mean(rets) * per_yr if per_yr else None
    ann_gross = 100 * statistics.mean(g_alps) * per_yr if (g_alps and per_yr) else None
    ann_net = 100 * statistics.mean(n_alps) * per_yr if (n_alps and per_yr) else None
    ann_cost = 100 * cost * per_yr if per_yr else None
    return {
        "n": len(rows),
        "avg_ret": round(100 * statistics.mean(rets), 3),
        "ret_t": round(_tstat(rets) or 0, 2),
        "avg_alpha_gross": round(100 * statistics.mean(g_alps), 3) if g_alps else None,
        "avg_alpha": round(100 * statistics.mean(n_alps), 3) if n_alps else None,
        "alpha_t": round(_tstat(n_alps) or 0, 2) if n_alps else None,
        "alpha_gross_t": round(_tstat(g_alps) or 0, 2) if g_alps else None,
        "ann_ret": round(ann_ret, 2) if ann_ret is not None else None,
        "ann_gross": round(ann_gross, 2) if ann_gross is not None else None,
        "ann_alpha": round(ann_net, 2) if ann_net is not None else None,
        "ann_cost": round(ann_cost, 2) if ann_cost is not None else None,
        "ann_net": round(ann_net, 2) if ann_net is not None else None,
        "win": round(100 * sum(1 for x in rets if x > 0) / len(rets), 2),
        "median": round(100 * statistics.median(rets), 3),
        "hold_med": int(statistics.median(holds)),
        "hold_mean": round(statistics.mean(holds), 1),
        "turnover_yr": round(244.0 / statistics.mean(holds), 2) if statistics.mean(holds) else None,
        "censored": sum(1 for r in rows if r["censored"]),
    }


def run(slip_bp=0.0, workers=8, write=True, first_only=False, entry_until=None,
        horizon_cap=None, bench="ew"):
    global BENCH
    BENCH = bench
    per = load_signals()
    jobs = []
    for c, g in per.items():
        if not g["buy"]:
            continue
        buys = sorted(g["buy"])
        if entry_until:
            buys = [d for d in buys if d <= entry_until]
            if not buys:
                continue
        if first_only:
            buys = buys[:1]        # 每票只留最早一笔：消除重叠持仓对 t 的高估
        jobs.append((c, buys, g["sell"], slip_bp, horizon_cap,
                     {d: g["beta"].get(d) for d in buys}, bench))
    all_rows = []
    miss = []
    if workers <= 1:
        res = [work(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(work, jobs, chunksize=16))
    for code, rows, err in res:
        if err:
            miss.append(code)
        all_rows.extend(rows)

    modes = ["hold%d" % h for h in HOLDS] + ["sell_any", "sell_1s"]
    tab = {}
    for m in modes:
        rs = [r for r in all_rows if r["mode"] == m]
        tab[m] = _summ(rs, slip_bp / 1e4)
    # 完整样本口径：只算真的等到卖点、走完一整段的交易（排除右删失）
    for m in ("sell_any", "sell_1s"):
        rs = [r for r in all_rows if r["mode"] == m and not r["censored"]]
        tab[m + "_done"] = _summ(rs, slip_bp / 1e4)

    # 配对口径：只保留 sell_any 与 hold20 都存在的信号 —— 严格可比
    have = defaultdict(set)
    for r in all_rows:
        have[(r["code"], r["d0"])].add(r["mode"])
    pair_keys = {k for k, v in have.items() if "hold20" in v and "sell_any" in v}
    paired = {}
    for m in ("hold20", "sell_any", "sell_1s"):
        rs = [r for r in all_rows if r["mode"] == m and (r["code"], r["d0"]) in pair_keys]
        paired[m] = _summ(rs, slip_bp / 1e4)

    # 配对差：同一批信号，sell_any − hold20
    d_by_key = {}
    for r in all_rows:
        if (r["code"], r["d0"]) in pair_keys:
            d_by_key.setdefault((r["code"], r["d0"]), {})[r["mode"]] = r["ret"] - (BUY_COST + SELL_COST) - 2 * slip_bp / 1e4
    diffs, diff_a = [], []
    for k, v in d_by_key.items():
        if "sell_any" in v and "hold20" in v:
            diffs.append(v["sell_any"] - v["hold20"])
    # alpha 差要看同一笔的 idx 抵消后仍不同（持期不同→idx 不同），需重算
    idxmap = {(r["code"], r["d0"], r["mode"]): r["idx"] for r in all_rows}
    for k, v in d_by_key.items():
        a1 = idxmap.get((k[0], k[1], "sell_any"))
        a0 = idxmap.get((k[0], k[1], "hold20"))
        if a1 is not None and a0 is not None:
            diff_a.append((v["sell_any"] - a1) - (v["hold20"] - a0))

    res = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "span": [SPAN_LO, SPAN_HI],
        "slip_bp": slip_bp,
        "bench": bench,
        "first_only": bool(first_only),
        "entry_until": entry_until,
        "horizon_cap": horizon_cap,
        "caliber": {
            "buy": "只 1买",
            "entry": "detect_date 之后第一个交易日开盘（T+1）",
            "exit_hold": "第 N 个交易日收盘",
            "exit_sell": "第一个卖点确认日的 T+1 开盘",
            "censor": "窗口末仍无卖点 → 末日收盘平仓，单列 censored",
            "cost": "买 0.075%% + 卖 0.125%%，滑点单边 %sbp 另计" % slip_bp,
            "alpha": "raw − β × EW全A 区间收益（与 chanlun_alpha 同源，非市值加权指数）",
        },
        "n_codes": len(jobs),
        "n_miss_bars": len(miss),
        "table": tab,
        "paired": paired,
        "pair_diff_sell_any_minus_hold20": {
            "n": len(diffs),
            "ret_diff": round(100 * statistics.mean(diffs), 3) if diffs else None,
            "ret_diff_t": round(_tstat(diffs) or 0, 2) if diffs else None,
            "alpha_diff": round(100 * statistics.mean(diff_a), 3) if diff_a else None,
            "alpha_diff_t": round(_tstat(diff_a) or 0, 2) if diff_a else None,
        },
        "note": "ret 是绝对收益（吃到 β），alpha 才是结构本身；持得越久 ret 越高不代表越好",
    }

    if write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    print("%-14s %6s %9s %7s %9s %8s %9s %7s" % (
        "口径", "n", "笔均毛超额", "毛年化", "年化成本", "净年化", "中位持期", "胜率"))
    for m in modes + [x + "_done" for x in ("sell_any", "sell_1s")]:
        s = tab[m]
        if not s:
            continue
        print("%-14s %6d %8.2f%% %8.2f%% %8.2f%% %8.2f%% %8d %6.1f%%" % (
            m, s["n"], s["avg_alpha_gross"] or 0, s["ann_gross"] or 0,
            s["ann_cost"] or 0, s["ann_net"] or 0, s["hold_med"], s["win"]))
    print()
    print("配对口径（同一批信号同时有两个口径）：")
    for m in ("hold20", "sell_any"):
        s = paired.get(m)
        if s:
            print("  %-10s n=%d 绝对 %.2f%% (t=%.2f) 超额 %.2f%% (t=%.2f)" % (
                m, s["n"], s["avg_ret"], s["ret_t"], s["avg_alpha"] or 0, s["alpha_t"] or 0))
    pd_ = res["pair_diff_sell_any_minus_hold20"]
    print("  差（卖点−hold20）：绝对 %+.3fpp (t=%.2f) ｜ 超额 %+.3fpp (t=%.2f)" % (
        pd_["ret_diff"] or 0, pd_["ret_diff_t"] or 0,
        pd_["alpha_diff"] or 0, pd_["alpha_diff_t"] or 0))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=0.0, help="单边滑点 bp")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--first-only", action="store_true",
                    help="每票只取最早一笔 1买，检验重叠样本是否把 t 抬高")
    ap.add_argument("--horizon-cap", type=int, default=None,
                    help="统一观察窗(交易日)：所有样本都看满该窗，彻底消除右删失")
    ap.add_argument("--entry-until", default=None,
                    help="只留该日期(8位)之前入场的样本，给长持期留足观察窗")
    ap.add_argument("--bench", default="ew", choices=("ew", "csi"),
                    help="ew=等权全A+逐笔β（正确口径）; csi=市值加权000985+β=1（旧口径对照）")
    ap.add_argument("--out", default=None, help="输出文件名，默认 portal/hf_sell_exit.json")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = os.path.join(ROOT, "portal", a.out)
    run(slip_bp=a.slip, workers=a.workers, write=not a.no_write,
        first_only=a.first_only, entry_until=a.entry_until,
        horizon_cap=a.horizon_cap, bench=a.bench)
    return 0


if __name__ == "__main__":
    sys.exit(main())
