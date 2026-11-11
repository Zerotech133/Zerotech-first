# -*- coding: utf-8 -*-
"""T4 · 神奇公式月度池（ROE×PE）独立验证（老田批准的研发计划任务，2026-11-11）。

口径（防前视是本任务最大坑，全部写死在代码里）：
- 财务 PIT：每月末重平衡日 T，只允许用 ann_date <= T 的最新财报（end_date 取该票
  已公告的最近期）。同一 (ts, end_date) 多行时取 ann_date 最大、再取 update_flag=1。
- ROE 年化：tushare roe 是报告期累计值，跨期不可比 → roe / 期数 * 12
  （03→3 个月、06→6、09→9、12→12）。
- 估值：重平衡日当日 pe_ttm（>0 才入池，负 PE 剔除）；市值过滤 total_mv>0。
- 收益：data/ashare 日线 pct_chg 累乘（含除权除息口径），20 交易日前瞻。
- 基准：全市场等权（与 vr_bucket_3buy 同源同口径，结论可直接对比）。

输出：
- 每月 top50 池超额（bp）、胜率、t；五分位单调性（因子有效性主判据）；
- reports/magic_pool_<today>.json。

验收门（研发计划）：池子独立超额 t<1 即停（不外推 PPT 的美股 40%）。
"""
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import datamart as dm

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "ashare")
TOP_N = 50
FWD = 20
MIN_POOL_MONTHS = 24  # 至少 24 个重平衡点才报 t（小样本不减信）

Q_COLS = {3: 3.0, 6: 6.0, 9: 9.0, 12: 12.0}  # end_date 月 → 期数


def load_close_matrix():
    """与 vr_bucket_3buy 同源：返回 (codes, dates, pct矩阵)。

    pct = pct_chg/100（部分旧文件缺列则 close/pre_close 兜底）。
    归一化起点无所谓：20 日前瞻只吃连续比值。
    """
    files = [f for f in os.listdir(DATA) if f.endswith(".parquet")]
    dates_all = set()
    chunks = {}
    for fn in files:
        code = fn[:-8].replace("_", ".")
        if code.endswith(".BJ"):
            continue
        df = pd.read_parquet(os.path.join(DATA, fn))
        if "pct_chg" in df.columns:
            p = df["pct_chg"].values / 100.0
        else:
            c = df["close"].values.astype(float)
            pc = df["pre_close"].values.astype(float)
            p = np.where(pc > 0, c / pc - 1.0, np.nan)
        d = df["trade_date"].astype(str).values
        chunks[code] = (d, p)
        dates_all.update(d.tolist())
    dates = sorted(dates_all)
    didx = {d: i for i, d in enumerate(dates)}
    mat = np.full((len(dates), len(chunks)), np.nan)
    for j, (code, (d, p)) in enumerate(sorted(chunks.items())):
        idx = np.array([didx[x] for x in d], dtype=int)
        ok = ~np.isnan(p)
        mat[idx[ok], j] = p[ok]
    codes = sorted(chunks)
    # 列序与 chunks 排序对齐
    col = {c: j for j, c in enumerate(sorted(chunks))}
    mat = mat[:, [col[c] for c in codes]]
    return codes, dates, mat


def build_fundamentals_pit():
    """财务 PIT 索引：ts -> [(ann_date, end_date, roe_ann), ...] 升序。"""
    f = dm.read("fina_indicator",
                fields=["ts_code", "end_date", "ann_date", "roe", "update_flag"])
    f = f.dropna(subset=["ann_date", "end_date", "roe"])
    f["ann_date"] = f["ann_date"].astype(str).str.slice(0, 8)
    f["end_date"] = f["end_date"].astype(str).str.slice(0, 8)
    m = f["end_date"].str[4:6].astype(int).map(Q_COLS)
    f = f[m.notna()]
    f["roe_ann"] = f["roe"] / m * 12.0  # 年化，跨期可比
    # 同 (ts,end_date) 取 ann_date 最新；同日取 update_flag=1（修正稿）
    f = f.sort_values(["ts_code", "end_date", "ann_date", "update_flag"])
    f = f.drop_duplicates(["ts_code", "end_date"], keep="last")
    f = f.sort_values(["ts_code", "ann_date"])
    pit = {}
    for ts, g in f.groupby("ts_code", sort=False):
        pit[ts] = list(zip(g["ann_date"].tolist(), g["end_date"].tolist(),
                           g["roe_ann"].tolist()))
    return pit


def roe_at(pit, ts, day):
    """PIT 取值：ann_date<=day 的最新一条。无 → None。"""
    lst = pit.get(ts)
    if not lst:
        return None
    lo, hi, best = 0, len(lst), None
    for ann, _end, r in lst:  # 列表已升序，直接线性扫（月度点少，量小）
        if ann <= day:
            best = r
        else:
            break
    return best


def main():
    t0 = time.time()
    today = time.strftime("%Y%m%d")

    print("== load close matrix (ashare) ==", flush=True)
    codes, dates, mat = load_close_matrix()
    cidx = {c: j for j, c in enumerate(codes)}
    print("codes=%d dates=%d (%s..%s) %.1fs"
          % (len(codes), len(dates), dates[0], dates[-1], time.time() - t0),
          flush=True)

    # 20 日前瞻矩阵（复合收益，正确口径）：L=对数价格累计（停牌日贡献 0），
    # fwd = expm1(L[d+FWD] - L[d])；未上市段（尚无任何数据）置 NaN。
    L = np.cumsum(np.log1p(np.nan_to_num(mat, nan=0.0)), axis=0)
    valid = np.cumsum(~np.isnan(mat), axis=0) > 0  # 该票已上市且有数据
    n = len(dates)
    Lf = np.vstack([L[FWD:], np.tile(L[-1:], (FWD, 1))])
    fwd = np.expm1(Lf - L)
    fwd[~valid] = np.nan
    fwd[n - FWD:, :] = np.nan
    lo23 = dates.index(next(d for d in dates if d >= "20230103"))
    cnt_fwd = (~np.isnan(fwd)).sum(axis=1)
    print("fwd20 ok, 2023后每日有效中位=%d"
          % np.median(cnt_fwd[lo23:n - FWD]), flush=True)

    print("== load fundamentals PIT ==", flush=True)
    pit = build_fundamentals_pit()
    print("pit ts=%d %.1fs" % (len(pit), time.time() - t0), flush=True)

    print("== load daily_basic ==", flush=True)
    b = dm.read("daily_basic",
                fields=["ts_code", "trade_date", "pe_ttm", "total_mv"])
    b["trade_date"] = b["trade_date"].astype(str).str.slice(0, 8)
    print("rows=%d ts=%d %.1fs" % (len(b), b.ts_code.nunique(), time.time() - t0),
          flush=True)

    # 重平衡日：每月最后一个交易日（来自 daily_basic 日历）
    cal = sorted(b["trade_date"].unique())
    month_last = {}
    for d in cal:
        month_last[d[:6]] = d  # 排序后最后一个即月末
    rebalance_days = sorted(set(month_last.values()))
    # 只留 2023 之后（daily_basic 窗口）且前瞻完整
    rebalance_days = [d for d in rebalance_days
                      if d >= "202301" and dates.index(d) + FWD < n]
    print("rebalance days: %d (%s..%s)" % (len(rebalance_days),
                                           rebalance_days[0],
                                           rebalance_days[-1]), flush=True)

    b_idx = b.set_index(["trade_date", "ts_code"])

    monthly = []
    quint = {q: [] for q in range(1, 6)}  # 五分位当月池超额
    pools = {}  # day -> top50 列表（供后续「基本面池∩缠论信号」复用）

    for day in rebalance_days:
        try:
            snap = b_idx.xs(day, level="trade_date")
        except KeyError:
            continue
        snap = snap[(snap["pe_ttm"] > 0) & (snap["total_mv"] > 0)].dropna(
            subset=["pe_ttm"])
        if len(snap) < 300:
            continue
        rows = []
        didx = dates.index(day)
        mkt_row = fwd[didx]
        mkt_ok = mkt_row[~np.isnan(mkt_row)]
        for ts, r in snap.iterrows():
            roe = roe_at(pit, ts, day)
            if roe is None:
                continue
            j = cidx.get(ts)
            if j is None or np.isnan(fwd[didx, j]):
                continue
            rows.append((ts, roe, float(r["pe_ttm"]), float(fwd[didx, j])))
        if len(rows) < 300:
            continue
        df = pd.DataFrame(rows, columns=["ts", "roe", "pe", "fwd"])
        df["rk_roe"] = df["roe"].rank(ascending=False)      # ROE 高好
        df["rk_pe"] = df["pe"].rank(ascending=True)         # PE 低好
        df["score"] = df["rk_roe"] + df["rk_pe"]
        df = df.sort_values("score")
        top = df.head(TOP_N)
        pools[day] = top["ts"].tolist()
        ex = (top["fwd"].mean() - df["fwd"].mean()) * 1e4
        monthly.append({"day": day, "n_univ": len(df), "excess_bp": round(ex, 1),
                        "pool_ret_bp": round(top["fwd"].mean() * 1e4, 1),
                        "mkt_ret_bp": round(df["fwd"].mean() * 1e4, 1),
                        "pool_win": round(float((top["fwd"] > df["fwd"].mean())
                                                .mean()), 3)})
        # 五分位（按综合分）：q1=分最好
        df["q"] = pd.qcut(df["score"].rank(method="first"), 5,
                          labels=[1, 2, 3, 4, 5])
        gmean = df.groupby("q", observed=True)["fwd"].mean()
        for q in range(1, 6):
            quint[q].append((gmean.get(q, np.nan) - df["fwd"].mean()) * 1e4)

    # 汇总
    exs = np.array([m["excess_bp"] for m in monthly], dtype=float)
    res = {
        "date": today,
        "task": "T4 神奇公式月度池（ROE年化×PE）独立验证",
        "pit_rule": "ann_date<=重平衡日；同(ts,end_date)取ann_date最新+update_flag=1；roe年化=roe/期数*12",
        "universe": "daily_basic 当日 pe_ttm>0 且 total_mv>0",
        "n_months": len(monthly),
        "window": "%s..%s" % (monthly[0]["day"], monthly[-1]["day"]) if monthly else None,
        "mean_excess_bp": round(float(exs.mean()), 1) if len(exs) else None,
        "t": round(float(exs.mean() / (exs.std(ddof=1) / math.sqrt(len(exs)))), 2) if len(exs) >= MIN_POOL_MONTHS else "样本不足24月不报t",
        "win_rate": round(float((exs > 0).mean()), 3) if len(exs) else None,
        "monthly": monthly,
        "quintile_mean_bp": {q: round(float(np.nanmean(v)), 1)
                             for q, v in quint.items()},
        "quintile_note": "q1=综合分最好（ROE高PE低）→ 单调递减为因子有效",
        "rebalance_dates": rebalance_days,
        "pools_top50": {d: pools[d] for d in sorted(pools)},
        "runtime_s": round(time.time() - t0, 1),
    }
    out = os.path.join(HERE, "reports", "magic_pool_%s.json" % today)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("\n== 结果 ==")
    print("月数:", res["n_months"], "窗口:", res["window"])
    print("月均超额: %s bp | t: %s | 胜率: %s"
          % (res["mean_excess_bp"], res["t"], res["win_rate"]))
    print("五分位超额(bp):", res["quintile_mean_bp"])
    print("落盘:", out)
    print("总用时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
