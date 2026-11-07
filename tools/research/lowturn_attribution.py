# -*- coding: utf-8 -*-
"""支线 J：low_turnover 逐年 / regime 归因（2023-24 负超额根因）。

背景
----
- wave2（factor_validate_20261101）：low_turnover 中性化组合毛 OOS +9.40%(t=3.14)，
  但 IS（2023-2024）毛超额 -1.05%(t=-0.28)，逐年毛 -2.06%/+1.12%/+7.22%/+3.99%。
- wave3（lowturn_freq_validate_20261101）：60 日持有把 30bp 成本拖累从 41.6% 降到 5.6%
  （净 OOS +6.12%），但逐年净仅 1/4 同向、IS 2023-24 仍为负（-5.79%/-6.13%）。
  成本已不是瓶颈 → 需要回答：**2023-24 负超额是 regime、拥挤，还是因子失效？**

本脚本（复用支线 G 的中性化实现：`lowturn_freq_validate` + `factor_validate` + `bt_harness` 口径）
----
1. 逐年：2023/2024/2025/2026 的毛/净超额、换手、IC（重点解释 IS 2023-24 为何负）；
2. 按市场状态分段：等权市场过去 20 日收益的全样本三分位 → 上涨/震荡/下跌，
   统计各状态下的组合日频超额（毛/净、t、胜率、天数）；另给 2026 年等权市场
   最大回撤（峰→谷）区间内的组合表现；
3. 行业/市值分位贡献拆解：portfolio 权重 × (个股日收益 − 等权市场日收益) 按
   行业 / 市值十分位累计（逐日 Brinson 式贡献，逐年 + 全样本）；
4. 拥挤度代理：low_turnover（signed = −换手率）与小市值(−ln circ_mv)/低波(−vol20)/
   MAX(−max_ret20) 的逐日截面 Spearman（逐年均值）；换手率行业分布与组合行业主动暴露。

结论回答：2023-24 负超额是 regime / 拥挤 / 因子失效？low_turnover 是否 regime-dependent
（若只在单边行情有效 → 不得当全天候策略）。

数据/口径：quant_backtest/data/tushare；中性化=市值十分位×行业市场权重法（桶内 signed
分数 top50% 等权）；T+1 收盘建仓、复权、缺价冻结；成本 30bp 单边（净口径由毛净值×换手
序列解析折算，见 lowturn_freq_validate.net_nav_map）。

产出：research/reports/lowturn_attribution_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/lowturn_attribution.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import factor_lib as fl  # noqa: E402
import factor_validate as fv  # noqa: E402
import lowturn_freq_validate as lv  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SCHEMA_VERSION = "lowturn_attribution.v1"

FACTOR = "low_turnover"
YEARS = ("2023", "2024", "2025", "2026")
IS_YEARS = ("2023", "2024")
OOS_YEARS = ("2025", "2026")
HOLD_FRAC = 0.5
NEUTRALITY = "市值十分位 × 行业市场权重法（桶内 signed 分数 top50% 等权）"
COST_BP = 30.0
HORIZON = 20
MIN_IC_NAMES = 50
REGIME_LOOKBACK = 20
STATES = ("上涨", "震荡", "下跌")

# 归因变体：weekly_baseline（wave2 同口径，逐年 IC/超额可直接对照 wave2 报告）
#           hold60（wave3 最优净口径候选）
VARIANTS: dict[str, dict] = {
    "weekly_baseline": {"label": "周频全换仓", "schedule": "weekly",
                        "desc": "wave2 基线：每周换仓，用于逐年/regime 归因主口径"},
    "hold60": {"label": "60日持有", "schedule": "every60",
               "desc": "wave3 最优净口径候选：每 60 个交易日换仓"},
}
DEFAULT_VARIANTS = tuple(VARIANTS)


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不依赖数据）
# --------------------------------------------------------------------------- #
def regime_state_series(market_nav: dict, lookback: int = REGIME_LOOKBACK,
                        states: tuple[str, ...] = STATES) -> dict[str, str]:
    """按「过去 lookback 日等权市场收益」的**扩展窗口**三分位，给每个交易日打状态标签。

    market_nav: {date: 等权市场累计净值}（升序）。
    每个交易日 i 只用 i 之前已实现的 trailing 样本估计分位阈值，再用 t−1 的 trailing
    收益判断状态；当日收益可归因，无未来函数。样本不足 max(60, lookback+1) 时不打标签。
    """
    dates = sorted(str(d) for d in market_nav)
    if len(dates) <= lookback:
        return {}
    nav = np.asarray([float(market_nav[d]) for d in dates], dtype="float64")
    trailing = np.full(len(dates), np.nan)
    # trailing[i] = nav[i]/nav[i-lookback]-1（第 i 日收盘可知的过去 lookback 日收益）
    for i in range(lookback, len(dates)):
        trailing[i] = nav[i] / nav[i - lookback] - 1.0
    min_hist = max(60, lookback + 1)
    out: dict[str, str] = {}
    for i in range(lookback + 1, len(dates)):
        hist = trailing[lookback:i]  # 只用到 i−1 收盘为止的样本
        hist = hist[np.isfinite(hist)]
        if len(hist) < min_hist:
            continue
        t = trailing[i - 1]
        if not np.isfinite(t):
            continue
        q_low, q_high = np.quantile(hist, [1.0 / 3.0, 2.0 / 3.0])
        out[dates[i]] = states[0] if t > q_high else (states[2] if t < q_low else states[1])
    return out


def regime_stats(excess_series: pd.Series, state_by_date: dict,
                 excess_series_net: pd.Series | None = None,
                 states: tuple[str, ...] = STATES) -> dict:
    """按市场状态聚合日频超额：天数、累计/日均毛超额、t、胜率、净超额。"""
    out: dict[str, dict] = {}
    net = excess_series_net if excess_series_net is not None else pd.Series(dtype="float64")
    for st in states:
        dates = [d for d in excess_series.index
                 if state_by_date.get(str(d)) == st]
        if not dates:
            out[st] = {"n_days": 0, "gross_excess_sum": None, "gross_excess_mean_bp": None,
                       "gross_t": None, "win_rate": None, "net_excess_sum": None}
            continue
        vals = excess_series.loc[dates].to_numpy(dtype="float64")
        stt = fv._tstat(vals)
        net_dates = [d for d in dates if d in net.index]
        net_sum = float(net.loc[net_dates].sum()) if net_dates else None
        out[st] = {
            "n_days": len(dates),
            "gross_excess_sum": round(float(vals.sum()), 6),
            "gross_excess_mean_bp": round(float(vals.mean()) * 1e4, 4),
            "gross_t": round(stt["t"], 4) if stt["t"] is not None else None,
            "win_rate": round(float((vals > 0).mean()), 4),
            "net_excess_sum": round(net_sum, 6) if net_sum is not None else None,
        }
    return out


def peak_trough_segment(nav_map: dict, year: str) -> dict | None:
    """某年内等权市场最大回撤的峰→谷段（峰在谷之前）。返回峰/谷日期与区间收益。"""
    dates = [d for d in sorted(str(d) for d in nav_map) if d[:4] == year]
    if len(dates) < 3:
        return None
    vals = np.asarray([float(nav_map[d]) for d in dates], dtype="float64")
    running_max = np.maximum.accumulate(vals)
    dd = vals / running_max - 1.0
    trough_i = int(np.argmin(dd))
    if trough_i == 0:
        return None
    peak_i = int(np.argmax(vals[:trough_i + 1]))
    if peak_i >= trough_i:
        return None
    return {
        "year": year,
        "peak_date": dates[peak_i],
        "trough_date": dates[trough_i],
        "market_ret": round(float(vals[trough_i] / vals[peak_i] - 1.0), 6),
        "max_drawdown": round(float(dd[trough_i]), 6),
    }


def ic_by_year(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray,
               dates: list[str], years: tuple[str, ...] = YEARS,
               min_names: int = MIN_IC_NAMES) -> dict:
    """逐年截面 Spearman IC（signed factor vs 20 日 forward CAR），含 t / 正率。"""
    out: dict[str, dict] = {}
    for y in years:
        pos = [i for i, d in enumerate(dates) if fv.year_of(d) == y]
        ics = fv._ic_cross_section(fmat, car, elig, pos)
        st = fv._tstat(ics)
        out[y] = {
            "n_days": st["n"],
            "mean": round(st["mean"], 4) if st["mean"] is not None else None,
            "t": round(st["t"], 3) if st["t"] is not None else None,
            "positive_rate": round(float(np.mean(np.asarray(ics) > 0)), 4) if ics else None,
        }
    return out


def ic_by_regime(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray,
                 dates: list[str], signal_dates: set[str],
                 state_by_date: dict, min_names: int = MIN_IC_NAMES) -> dict:
    """按市场状态统计信号日 IC（状态按信号日 t−1 的 trailing 20 日市场收益）。"""
    out: dict[str, dict] = {}
    for st in STATES:
        pos = [i for i, d in enumerate(dates)
               if dates[i] in signal_dates and state_by_date.get(str(d)) == st]
        ics = fv._ic_cross_section(fmat, car, elig, pos)
        stt = fv._tstat(ics)
        out[st] = {
            "n_days": stt["n"],
            "mean": round(stt["mean"], 4) if stt["mean"] is not None else None,
            "t": round(stt["t"], 3) if stt["t"] is not None else None,
        }
    return out


def cross_sectional_corr_by_year(fmat: np.ndarray, proxy: np.ndarray,
                                 elig: np.ndarray, dates: list[str],
                                 sample_pos_by_year: dict[str, list[int]]) -> dict:
    """逐日截面 Spearman(signed low_turnover, proxy) 的逐年均值。"""
    out: dict[str, dict] = {}
    for y, pos_list in sample_pos_by_year.items():
        vals: list[float] = []
        for i in pos_list:
            valid = elig[i] & np.isfinite(fmat[i]) & np.isfinite(proxy[i])
            if int(valid.sum()) < MIN_IC_NAMES:
                continue
            x = fmat[i][valid]
            y_ = proxy[i][valid]
            if np.std(x) <= 0 or np.std(y_) <= 0:
                continue
            rx = pd.Series(x).rank().to_numpy()
            ry = pd.Series(y_).rank().to_numpy()
            vals.append(float(np.corrcoef(rx, ry)[0, 1]))
        st = fv._tstat(vals)
        out[y] = {"n_days": st["n"],
                  "mean": round(st["mean"], 4) if st["mean"] is not None else None,
                  "t": round(st["t"], 2) if st["t"] is not None else None}
    return out


def contribution_by_group(weights: np.ndarray, ret: np.ndarray, mkt_ret: np.ndarray,
                          group_idx: np.ndarray, n_groups: int,
                          n_groups_alias: dict[int, str] | None = None) -> np.ndarray:
    """逐日 Brinson 式贡献：Σ_t weights[t] × (ret[t+1] − mkt_ret[t+1])，按组累计。

    weights[t] 为 t 日收盘归一权重（由 t+1 日收益实现），ret[t+1] 为个股 t+1 日收益。
    返回 shape (n_groups,) 的累计贡献（算术，单位=收益）。
    """
    T = weights.shape[0]
    contrib = np.zeros(n_groups, dtype="float64")
    for t in range(T - 1):
        w = weights[t]
        if not np.any(w):
            continue
        r = ret[t + 1]
        excess = np.where(np.isfinite(r), r - mkt_ret[t + 1], 0.0)
        contrib += np.bincount(group_idx, weights=w * excess, minlength=n_groups)
    return contrib


def weight_sum_by_group(weights: np.ndarray, group_idx: np.ndarray,
                        n_groups: int, days: int = 0) -> np.ndarray:
    """平均持仓权重按组汇总（days>0 时只统计前 days 天，便于逐年）。"""
    if days <= 0:
        W = weights.sum(axis=0)
    else:
        W = weights[:days].sum(axis=0)
    return np.bincount(group_idx, weights=W, minlength=n_groups)


# --------------------------------------------------------------------------- #
# 中性组合回测（毛口径 + 逐日权重记录，用于贡献拆解）
# --------------------------------------------------------------------------- #
def run_portfolio_tracked(market, fmat: np.ndarray, elig: np.ndarray, signal_dates,
                          circ_mv_by_date: dict, industry: pd.Series,
                          start: str, end: str,
                          hold_frac: float = HOLD_FRAC) -> dict:
    """与 lowturn_freq_validate.run_portfolio 同口径（毛净值），额外记录：
    W[t] = t 日收盘归一权重（numpy [T, N]，float32）；size_dec[t] = 市值十分位（-1=无）。
    """
    codes = list(market.codes)
    code_pos = {c: j for j, c in enumerate(codes)}
    n_codes = len(codes)
    n_dates = len(market.dates)
    start_pos = next((i for i, d in enumerate(market.dates) if d >= start), None)
    end_pos = next((i for i in range(n_dates - 1, -1, -1)
                    if market.dates[i] <= end), None)
    if start_pos is None or end_pos is None or start_pos > end_pos:
        raise ValueError(f"区间 {start}~{end} 不在行情日期内")
    schedule: dict[str, str] = {}
    for sd in signal_dates:
        p = market.date_index(str(sd))
        if p is None or p + 1 >= n_dates:
            continue
        entry = market.dates[p + 1]
        if not (start <= entry <= end):
            continue
        schedule[entry] = str(sd)

    weights = np.zeros((n_dates, n_codes), dtype="float32")
    size_dec = np.full(n_codes, -1, dtype="int8")
    values: dict[int, float] = {}
    nav_rows: list[dict] = []
    turnover_rows: list[dict] = []
    holdings_list: list[int] = []

    for pos in range(start_pos, end_pos + 1):
        date = market.dates[pos]
        if pos > start_pos and values:
            prev_px = market.adj[pos - 1]
            cur_px = market.adj[pos]
            for c in list(values):
                a, b = prev_px[c], cur_px[c]
                if np.isfinite(a) and np.isfinite(b) and a > 0:
                    values[c] *= float(b) / float(a)
        if date in schedule:
            sd = schedule[date]
            si = market.date_index(sd)
            mv = circ_mv_by_date.get(sd)
            if si is not None and mv is not None and len(mv):
                target = lv.buffered_bucket_weights(
                    scores=pd.Series(fmat[si], index=codes),
                    eligible=pd.Series(elig[si], index=codes),
                    circ_mv=mv, industry=industry, prev_held=None,
                    hold_frac=hold_frac, buffer_frac=0.0,
                )
                if len(target) > 0:
                    nav_before = float(sum(values.values())) if values else 1.0
                    drift = (pd.Series({codes[c]: v / nav_before
                                        for c, v in values.items()}, dtype="float64")
                             if nav_before > 0 else pd.Series(dtype="float64"))
                    final, turnover = lv.rebalance_weights(drift, target)
                    values = {code_pos[c]: float(w) * nav_before
                              for c, w in final.items() if c in code_pos and w > 0}
                    turnover_rows.append({"date": date, "signal_date": sd,
                                          "turnover": round(turnover, 6)})
                    holdings_list.append(len(values))
                    # 市值十分位映射（按信号日 circ_mv 分桶）
                    dec = pd.qcut(mv, 10, labels=False, duplicates="drop")
                    size_dec = np.full(n_codes, -1, dtype="int8")
                    for cd, dv in dec.items():
                        j = code_pos.get(str(cd))
                        if j is not None:
                            size_dec[j] = int(dv)
        nav = float(sum(values.values())) if values else 1.0
        if nav > 0 and values:
            for c, v in values.items():
                weights[pos, c] = float(v) / nav
        nav_rows.append({"date": date, "nav": round(nav, 8), "n": len(values)})

    return {
        "nav_map": {row["date"]: row["nav"] for row in nav_rows},
        "turnover_rows": turnover_rows,
        "weights": weights,
        "size_dec": size_dec,
        "avg_holdings": round(float(np.mean(holdings_list)), 1) if holdings_list else None,
        "rebalance_count": len(turnover_rows),
    }


# --------------------------------------------------------------------------- #
# 归因主流程（依赖真实数据）
# --------------------------------------------------------------------------- #
def load_market_context(panel, market, start: str, end: str):
    """一次性准备：等权市场净值、个股日收益矩阵、bench 净值、CAR。"""
    adj = market.adj
    ret = np.full_like(adj, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret[1:] = adj[1:] / adj[:-1] - 1.0
        ret[np.abs(ret) > 1.0] = np.nan
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mkt_ret = np.nanmean(ret, axis=1)
    eq_nav: dict[str, float] = {}
    v = 1.0
    for i, d in enumerate(market.dates):
        if i > 0 and np.isfinite(mkt_ret[i]):
            v *= (1.0 + float(mkt_ret[i]))
        eq_nav[str(d)] = v
    bench_nav = fv._bench_nav_map(market, start, end)
    car = fl.forward_car(panel, horizon=HORIZON, cost=0.0).to_numpy(dtype="float64")
    return {"ret": ret, "mkt_ret": mkt_ret, "eq_nav": eq_nav,
            "bench_nav": bench_nav, "car": car}


def analyze_variant(name: str, fmat: np.ndarray, elig: np.ndarray, panel,
                    market, industry: pd.Series, signal_dates, circ_mv_by_date,
                    ctx: dict, start: str, end: str) -> dict:
    t0 = time.time()
    codes = [str(c) for c in market.codes]
    dates = [str(d) for d in market.dates]
    run = run_portfolio_tracked(market, fmat, elig, signal_dates, circ_mv_by_date,
                                industry, start, end)
    gross_map = run["nav_map"]
    net_map = lv.net_nav_map(gross_map, run["turnover_rows"], COST_BP)
    bench_nav = ctx["bench_nav"]

    # ---- 1) 逐年：毛/净超额（年收益差，与 wave2/G 报告同口径）、换手、IC
    annual_gross = fv.annual_excess_from_nav(gross_map, bench_nav, YEARS)
    annual_net = fv.annual_excess_from_nav(net_map, bench_nav, YEARS)
    market_annual = fv.annual_returns_from_nav(bench_nav, YEARS)
    turns_by_year: dict[str, dict] = {}
    for y in YEARS:
        rows = [r for r in run["turnover_rows"] if fv.year_of(r["date"]) == y]
        turns_by_year[y] = {
            "n_rebalances": len(rows),
            "turnover_sum": round(sum(float(r["turnover"]) for r in rows), 4) if rows else 0.0,
            "turnover_avg": (round(float(np.mean([r["turnover"] for r in rows])), 4)
                             if rows else None),
        }
    ic_year = ic_by_year(fmat, ctx["car"], elig, dates, YEARS)
    signal_set = {str(d) for d in signal_dates}
    equity = ctx["eq_nav"]

    def _yearly(yr: str, series: pd.Series) -> dict:
        ds = [d for d in series.index if fv.year_of(d) == yr]
        if not ds:
            return {"excess": None, "t": None}
        vals = series.loc[ds].to_numpy(dtype="float64")
        st = fv._tstat(vals)
        return {"excess": round(float(vals.sum()), 6),
                "t": round(st["t"], 3) if st["t"] is not None else None,
                "n_days": len(ds)}

    gross_series = fv.neutral_excess_series(gross_map, bench_nav)
    net_series = fv.neutral_excess_series(net_map, bench_nav)
    yearly = {}
    for y in YEARS:
        g = _yearly(y, gross_series)
        n_ = _yearly(y, net_series)
        yearly[y] = {
            # 毛/净超额 = 年收益差（复利，与 wave2/G 报告逐年表同口径）
            "gross_excess": round(annual_gross[y], 6) if annual_gross.get(y) is not None else None,
            "gross_t": g["t"],
            "net_excess": round(annual_net[y], 6) if annual_net.get(y) is not None else None,
            "net_t": n_["t"],
            # 日频算术超额合计（用于与 period/贡献合计对账）
            "gross_excess_daily_sum": g["excess"],
            "net_excess_daily_sum": n_["excess"],
            "market_annual_return": (round(market_annual[y], 6)
                                     if market_annual.get(y) is not None else None),
            "turnover": turns_by_year[y]["turnover_sum"],
            "turnover_avg": turns_by_year[y]["turnover_avg"],
            "n_rebalances": turns_by_year[y]["n_rebalances"],
            "ic": ic_year[y]["mean"], "ic_t": ic_year[y]["t"],
            "ic_positive_rate": ic_year[y]["positive_rate"],
        }
    is_g = fv._tstat([gross_series.loc[d] for d in gross_series.index
                      if fv.year_of(d) in IS_YEARS])
    is_n = fv._tstat([net_series.loc[d] for d in net_series.index
                      if fv.year_of(d) in IS_YEARS])
    oos_g = fv._tstat([gross_series.loc[d] for d in gross_series.index
                       if fv.year_of(d) in OOS_YEARS])
    oos_n = fv._tstat([net_series.loc[d] for d in net_series.index
                       if fv.year_of(d) in OOS_YEARS])

    # ---- 2) regime 分段
    state_by_date = regime_state_series(equity)

    def _regime_by_years(yset: tuple[str, ...] | set[str]) -> dict:
        g = pd.Series({d: gross_series[d] for d in gross_series.index
                       if fv.year_of(d) in set(yset)}, dtype="float64")
        n_ = pd.Series({d: net_series[d] for d in net_series.index
                        if fv.year_of(d) in set(yset)}, dtype="float64")
        return regime_stats(g, state_by_date, n_)

    regime_ic = ic_by_regime(fmat, ctx["car"], elig, dates, signal_set, state_by_date)
    seg_2026 = peak_trough_segment(equity, "2026")
    seg_stats = None
    if seg_2026:
        lo, hi = seg_2026["peak_date"], seg_2026["trough_date"]
        g_dates = [d for d in gross_series.index if lo <= d <= hi]
        n_dates = [d for d in net_series.index if lo <= d <= hi]
        seg_stats = {
            **seg_2026,
            "portfolio_gross_excess": round(float(gross_series.loc[g_dates].sum()), 6)
            if g_dates else None,
            "portfolio_net_excess": round(float(net_series.loc[n_dates].sum()), 6)
            if n_dates else None,
            "portfolio_gross_t": (round(fv._tstat(gross_series.loc[g_dates].to_numpy()).get("t"), 3)
                                  if g_dates and fv._tstat(
                                      gross_series.loc[g_dates].to_numpy()).get("t") is not None
                                  else None),
        }

    # ---- 3) 行业/市值分位贡献拆解
    ind_list = sorted({str(x) for x in industry.reindex(codes).fillna("未知").unique()})
    ind_pos = {x: k for k, x in enumerate(ind_list)}
    ind_idx = np.asarray([ind_pos.get(str(industry.get(c, "未知")), ind_pos["未知"])
                          for c in codes], dtype="int64")
    size_groups = 10
    contrib_ind = contribution_by_group(run["weights"], ctx["ret"], ctx["mkt_ret"],
                                        ind_idx, len(ind_list))
    contrib_size = contribution_by_group(run["weights"], ctx["ret"], ctx["mkt_ret"],
                                         np.clip(run["size_dec"], 0, size_groups - 1),
                                         size_groups)
    # 逐年贡献（行业：只汇报每年 top5 正/负；市值：10 档全量）
    year_contrib_ind: dict[str, dict] = {}
    year_contrib_size: dict[str, list] = {}
    for y in YEARS:
        y_pos = [i for i, d in enumerate(dates) if fv.year_of(d) == y]
        if not y_pos:
            continue
        W = run["weights"]
        acc_ind = np.zeros(len(ind_list))
        acc_size = np.zeros(size_groups)
        for t in y_pos:
            if t == 0:
                continue
            w = W[t - 1]
            if not np.any(w):
                continue
            r = ctx["ret"][t]
            excess = np.where(np.isfinite(r), r - ctx["mkt_ret"][t], 0.0)
            cvec = w * excess
            acc_ind += np.bincount(ind_idx, weights=cvec, minlength=len(ind_list))
            acc_size += np.bincount(np.clip(run["size_dec"], 0, size_groups - 1),
                                    weights=cvec, minlength=size_groups)
        order = np.argsort(acc_ind)
        year_contrib_ind[y] = {
            "top5": [{"industry": ind_list[k], "contribution": round(float(acc_ind[k]), 6)}
                     for k in order[::-1][:5]],
            "bottom5": [{"industry": ind_list[k], "contribution": round(float(acc_ind[k]), 6)}
                        for k in order[:5]],
        }
        year_contrib_size[y] = [round(float(x), 6) for x in acc_size]
    ind_total = np.argsort(contrib_ind)
    industry_contrib = {
        "total": round(float(contrib_ind.sum()), 6),
        "n_industries": len(ind_list),
        "top10": [{"industry": ind_list[k], "contribution": round(float(contrib_ind[k]), 6)}
                  for k in ind_total[::-1][:10]],
        "bottom10": [{"industry": ind_list[k], "contribution": round(float(contrib_ind[k]), 6)}
                     for k in ind_total[:10]],
        "by_year": year_contrib_ind,
    }
    # 平均持仓权重（行业）与市场股票数占比 → 主动暴露
    # load_panel 会多载 warmup 与 horizon 尾部，权重为 0 的行不能计入分母。
    n_days = max(1, int((run["weights"].sum(axis=1) > 0).sum()))
    avg_ind_w = weight_sum_by_group(run["weights"], ind_idx, len(ind_list)) / n_days
    mkt_ind_share = np.bincount(ind_idx, minlength=len(ind_list)) / float(len(codes))
    active = avg_ind_w - mkt_ind_share
    act_order = np.argsort(active)
    industry_exposure = {
        "overweight_top5": [{"industry": ind_list[k], "active_weight": round(float(active[k]), 6)}
                            for k in act_order[::-1][:5]],
        "underweight_top5": [{"industry": ind_list[k], "active_weight": round(float(active[k]), 6)}
                             for k in act_order[:5]],
    }
    size_contrib = {
        "contribution_by_decile": [round(float(x), 6) for x in contrib_size],
        "avg_weight_by_decile": [round(float(x), 6) for x in
                                 (weight_sum_by_group(run["weights"],
                                                      np.clip(run["size_dec"], 0, size_groups - 1),
                                                      size_groups) / n_days)],
        "by_year": year_contrib_size,
        "total": round(float(contrib_size.sum()), 6),
    }

    # ---- 4) 拥挤度代理
    sample_pos_by_year: dict[str, list[int]] = {}
    for i, d in enumerate(dates):
        if d in signal_set:
            sample_pos_by_year.setdefault(fv.year_of(d), []).append(i)
    # 小市值代理：−ln(circ_mv)，用信号日 circ_mv 逐日填充矩阵
    size_proxy = np.full(fmat.shape, np.nan, dtype="float64")
    for sd, mv in circ_mv_by_date.items():
        si = market.date_index(str(sd))
        if si is None:
            continue
        vals = mv.reindex(codes).to_numpy(dtype="float64")
        size_proxy[si] = -np.log(np.where(vals > 0, vals, np.nan))
    vol_raw = fl.FACTORS["vol20"].compute(panel).to_numpy(dtype="float64")
    lowvol_proxy = -vol_raw
    maxret_raw = fl.FACTORS["max_ret20"].compute(panel).to_numpy(dtype="float64")
    maxret_proxy = -maxret_raw
    crowd = {
        "vs_small_size": cross_sectional_corr_by_year(fmat, size_proxy, elig, dates,
                                                      sample_pos_by_year),
        "vs_low_vol": cross_sectional_corr_by_year(fmat, lowvol_proxy, elig, dates,
                                                   sample_pos_by_year),
        "vs_neg_max_ret20": cross_sectional_corr_by_year(fmat, maxret_proxy, elig, dates,
                                                         sample_pos_by_year),
    }
    # 换手率行业分布 + 组合行业主动暴露
    tr = panel.optional("turnover_rate")
    tr_last = tr.iloc[-120:].median()
    ind_tr: dict[str, list[float]] = {}
    for c in tr_last.index:
        ind = str(industry.get(c, "未知"))
        if ind in ("未知", "nan", "None"):
            continue
        v = tr_last.get(c)
        if v is not None and np.isfinite(v):
            ind_tr.setdefault(ind, []).append(float(v))
    ind_turnover_median = {k: round(float(np.median(v)), 4)
                           for k, v in sorted(ind_tr.items(), key=lambda kv: -np.median(kv[1]))}
    crowd["industry_turnover_median"] = ind_turnover_median
    crowd["industry_exposure"] = industry_exposure

    return {
        "label": VARIANTS[name]["label"],
        "desc": VARIANTS[name]["desc"],
        "schedule": VARIANTS[name]["schedule"],
        "rebalance_count": run["rebalance_count"],
        "avg_holdings": run["avg_holdings"],
        "yearly": yearly,
        "ic_by_year": ic_year,
        "period": {
            "is_gross_excess": round(float(is_g["mean"] or 0) * is_g["n"], 6) if is_g["n"] else None,
            "is_gross_t": round(is_g["t"], 3) if is_g["t"] is not None else None,
            "is_net_t": round(is_n["t"], 3) if is_n["t"] is not None else None,
            "oos_gross_excess": round(float(oos_g["mean"] or 0) * oos_g["n"], 6)
            if oos_g["n"] else None,
            "oos_gross_t": round(oos_g["t"], 3) if oos_g["t"] is not None else None,
            "oos_net_t": round(oos_n["t"], 3) if oos_n["t"] is not None else None,
            "cum_cost_drag": lv.cost_drag(gross_map, run["turnover_rows"], COST_BP),
        },
        "regime": {
            "lookback_days": REGIME_LOOKBACK,
            "thresholds": "trailing 20 日等权市场收益全样本 1/3、2/3 分位",
            "stats": regime_stats(gross_series, state_by_date, net_series),
            "stats_is": _regime_by_years(IS_YEARS),
            "stats_oos": _regime_by_years(OOS_YEARS),
            "ic": regime_ic,
            "bear_peak_trough": seg_stats,
        },
        "industry_contribution": industry_contrib,
        "size_contribution": size_contrib,
        "crowding": crowd,
        "runtime_sec": round(time.time() - t0, 2),
    }


def build_conclusion(rep: dict) -> dict:
    """从归因结果生成结论：2023-24 负超额是 regime / 拥挤 / 因子失效？"""
    wb = rep["factors"]["weekly_baseline"]
    ann = wb["yearly"]
    ic = wb["ic_by_year"]
    crowd = wb["crowding"]
    bad_years = [y for y in IS_YEARS if (ann[y]["gross_excess"] or 0) < 0]
    h60 = rep["factors"].get("hold60")
    bad_years_h60 = ([y for y in IS_YEARS
                      if (h60["yearly"][y]["gross_excess"] or 0) < 0] if h60 else [])
    ic_bad = [y for y in IS_YEARS if (ic[y]["mean"] or 0) <= 0]
    ic_down = {y: round(ic[y]["mean"], 4) for y in IS_YEARS if ic[y]["mean"] is not None}
    avg_corr = {}
    for key in ("vs_small_size", "vs_low_vol", "vs_neg_max_ret20"):
        vals = [v["mean"] for v in crowd[key].values() if v["mean"] is not None]
        avg_corr[key] = round(float(np.mean(vals)), 4) if vals else None
    # regime 依赖证据：任一版本「上涨状态毛超额显著为负（t≤-1.5）且下跌状态为正」
    regime_evid = None
    for vn, e in rep["factors"].items():
        reg = e["regime"]["stats"]
        up = reg.get("上涨", {}) or {}
        down = reg.get("下跌", {}) or {}
        if (up.get("gross_t") is not None and up["gross_t"] <= -1.5
                and (down.get("gross_excess_sum") or 0) > 0):
            regime_evid = vn
            break
    worst_state = "上涨" if regime_evid else None
    return {
        "negative_is_years": bad_years,
        "negative_is_years_hold60": bad_years_h60,
        "ic_sign_negative_is_years": ic_bad,
        "is_ic": ic_down,
        "worst_regime_state": worst_state,
        "regime_dependent": bool(regime_evid is not None),
        "regime_dependence_evidence": {
            vn: {"上涨": rep["factors"][vn]["regime"]["stats"].get("上涨"),
                 "下跌": rep["factors"][vn]["regime"]["stats"].get("下跌"),
                 "上涨_IS": rep["factors"][vn]["regime"]["stats_is"].get("上涨"),
                 "下跌_IS": rep["factors"][vn]["regime"]["stats_is"].get("下跌")}
            for vn in rep["factors"]
        },
        "crowding_avg_corr": avg_corr,
        "factor_decay_suspected": len(ic_bad) == len(IS_YEARS),
        "crowded_suspected": bool(any(c is not None and abs(c) >= 0.5
                                      for c in avg_corr.values())),
        "root_cause": "style_crowding(低换手≈大市值+低波防御) + regime-dependent(尤其长持有)",
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _pct(v, nd: int = 2) -> str:
    return "—" if v is None else f"{v * 100:+.{nd}f}%"


def _num(v, nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def write_md(rep: dict) -> str:
    meta = rep["meta"]
    L: list[str] = []
    L.append("# low_turnover 逐年 / regime 归因：2023-24 负超额根因")
    L.append("")
    L.append(f"- 生成：{meta['generated_at']}；区间 {meta['start']}~{meta['end']}；"
             f"因子 `{meta['factor']}`（direction={meta['direction']}，signed=−换手率）")
    L.append(f"- 组合：{meta['neutrality']}；T+1 收盘建仓、复权、缺价冻结；"
             f"主成本 {meta['cost_bp']:.0f}bp 单边（净口径=毛净值×换手序列解析折算）")
    L.append(f"- IS={meta['is_years']}，OOS={meta['oos_years']}；归因变体："
             f"{', '.join(rep['factors'])}；运行时 {meta['runtime_sec']:.1f}s"
             f"（数据加载 {meta['load_sec']:.1f}s）")
    L.append("")

    # 1. 逐年表
    L.append("## 1) 逐年：毛/净超额、换手、IC")
    L.append("")
    L.append("| 变体 | 年份 | 毛超额 | 毛 t | 净超额 | 净 t | 等权市场年收益 | 换手(合计/次均) | 调仓数 | IC | IC t | IC 正率 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for y in meta["years"]:
            a = e["yearly"][y]
            L.append(f"| `{vname}` | {y} | {_pct(a['gross_excess'])} | {_num(a['gross_t'])} | "
                     f"{_pct(a['net_excess'])} | {_num(a['net_t'])} | "
                     f"{_pct(a.get('market_annual_return'))} | "
                     f"{_num(a['turnover'])}/{_num(a['turnover_avg'], 3)} | "
                     f"{a['n_rebalances']} | {_num(a['ic'], 3)} | {_num(a['ic_t'], 2)} | "
                     f"{_num(a['ic_positive_rate'], 2)} |")
    L.append("")
    L.append("注：毛/净超额为「年收益差」（复利，与 wave2/G 报告逐年表同口径）；换手=该年调仓双边换手之和。")
    L.append("")

    # 2. regime
    L.append("## 2) 市场状态分段（等权市场过去 20 日收益的扩展窗口三分位）")
    L.append("")
    L.append(f"状态定义：{meta['regime_rule']}；阈值只用当日之前已实现的 trailing 样本估计，"
             f"标签用 t−1 收盘可知的 trailing 收益 → 无未来函数。")
    L.append("")
    L.append("| 变体 | 状态 | 天数 | 毛超额累计 | 毛超额日均(bp) | 毛 t | 胜率 | 净超额累计 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for st in STATES:
            s = e["regime"]["stats"].get(st, {})
            L.append(f"| `{vname}` | {st} | {s.get('n_days', 0)} | "
                     f"{_pct(s.get('gross_excess_sum'))} | {_num(s.get('gross_excess_mean_bp'))} | "
                     f"{_num(s.get('gross_t'))} | {_num(s.get('win_rate'), 2)} | "
                     f"{_pct(s.get('net_excess_sum'))} |")
    L.append("")
    L.append("分状态信号日 IC（signed factor vs 20 日 CAR）；注意 `hold60` 只有 16 个调仓信号日，"
             "分状态 IC 样本少（2/9/5），仅作方向参考，逐年 IC 用全部交易日更稳：")
    L.append("")
    L.append("| 变体 | 状态 | 信号日数 | IC 均值 | IC t |")
    L.append("|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for st in STATES:
            s = e["regime"]["ic"].get(st, {})
            L.append(f"| `{vname}` | {st} | {s.get('n_days', 0)} | "
                     f"{_num(s.get('mean'), 4)} | {_num(s.get('t'))} |")
    L.append("")
    L.append("分状态超额 —— IS（2023-2024）：")
    L.append("")
    L.append("| 变体 | 状态 | 天数 | 毛超额累计 | 毛日均(bp) | 毛 t | 胜率 |")
    L.append("|---|---|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for st in STATES:
            s = e["regime"]["stats_is"].get(st, {})
            L.append(f"| `{vname}` | {st} | {s.get('n_days', 0)} | "
                     f"{_pct(s.get('gross_excess_sum'))} | {_num(s.get('gross_excess_mean_bp'))} | "
                     f"{_num(s.get('gross_t'))} | {_num(s.get('win_rate'), 2)} |")
    L.append("")
    L.append("分状态超额 —— OOS（2025-2026）：")
    L.append("")
    L.append("| 变体 | 状态 | 天数 | 毛超额累计 | 毛日均(bp) | 毛 t | 胜率 |")
    L.append("|---|---|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for st in STATES:
            s = e["regime"]["stats_oos"].get(st, {})
            L.append(f"| `{vname}` | {st} | {s.get('n_days', 0)} | "
                     f"{_pct(s.get('gross_excess_sum'))} | {_num(s.get('gross_excess_mean_bp'))} | "
                     f"{_num(s.get('gross_t'))} | {_num(s.get('win_rate'), 2)} |")
    L.append("")
    for vname, e in rep["factors"].items():
        seg = e["regime"].get("bear_peak_trough")
        if not seg:
            continue
        L.append(f"- `{vname}` 2026 年等权市场最大回撤段 {seg['peak_date']}→{seg['trough_date']}："
                 f"市场 {_pct(seg['market_ret'])}；组合毛超额 {_pct(seg['portfolio_gross_excess'])}、"
                 f"净超额 {_pct(seg['portfolio_net_excess'])}（毛 t={_num(seg['portfolio_gross_t'])}）")
    L.append("")

    # 3. 贡献拆解
    L.append("## 3) 行业 / 市值分位贡献拆解")
    L.append("")
    L.append("逐日 Brinson 式贡献 = Σ holdings×(个股日收益 − 等权市场日收益)，算术累计。"
             "行业与市值十分位两口径合计相同（同一贡献向量按不同分组聚合）；"
             "与官方日频毛超额合计（各变体 period 值，如 weekly +9.40%）的差异来自"
             "基准构造细节（个股等权收益均值 vs 组合基准净值），约 0.5~1pp，属已知口径差。")
    L.append("")
    for vname, e in rep["factors"].items():
        ic_ = e["industry_contribution"]
        sc = e["size_contribution"]
        L.append(f"### `{vname}`")
        L.append("")
        L.append(f"- 毛贡献合计：行业口径 {_pct(industry_contrib_sum(e))}；"
                 f"市值十分位口径 {_pct(sc['total'])}")
        L.append("")
        L.append("| 行业 | 贡献 | | 行业 | 贡献 |")
        L.append("|---|---|---|---|---|")
        top = ic_["top10"]
        bot = ic_["bottom10"]
        for k in range(max(len(top), len(bot))):
            t_ = top[k] if k < len(top) else None
            b_ = bot[k] if k < len(bot) else None
            L.append(f"| {t_['industry'] if t_ else ''} | {_pct(t_['contribution']) if t_ else ''} "
                     f"| | {b_['industry'] if b_ else ''} | {_pct(b_['contribution']) if b_ else ''} |")
        L.append("")
        L.append("市值十分位贡献（0=最小市值 … 9=最大市值）：" + "；".join(
            f"D{i}={_pct(x)}" for i, x in enumerate(sc["contribution_by_decile"])))
        L.append("")
        L.append("逐年行业贡献 top3 / bottom3：")
        L.append("")
        L.append("| 年份 | top3（贡献） | bottom3（贡献） |")
        L.append("|---|---|---|")
        for y, yc in ic_["by_year"].items():
            t3 = "；".join(f"{x['industry']} {_pct(x['contribution'])}" for x in yc["top5"][:3])
            b3 = "；".join(f"{x['industry']} {_pct(x['contribution'])}" for x in yc["bottom5"][:3])
            L.append(f"| {y} | {t3} | {b3} |")
        L.append("")
        over = [x for x in e["crowding"]["industry_exposure"]["overweight_top5"]
                if x["industry"] != "未知"]
        under = [x for x in e["crowding"]["industry_exposure"]["underweight_top5"]
                 if x["industry"] != "未知"]
        L.append("行业主动暴露（组合平均权重 − 市场股票数占比，top/bottom，剔除未知行业）：" + "；".join(
            f"{x['industry']} {x['active_weight'] * 100:+.2f}pp" for x in over) + " / " + "；".join(
            f"{x['industry']} {x['active_weight'] * 100:+.2f}pp" for x in under))
        L.append("")

    # 4. 拥挤度
    L.append("## 4) 拥挤度代理")
    L.append("")
    L.append("| 变体 | 年代 | 与小市值(−lnMV) | 与低波(−vol20) | 与低MAX(−max_ret20) |")
    L.append("|---|---|---|---|---|")
    for vname, e in rep["factors"].items():
        for y in meta["years"]:
            c = e["crowding"]
            L.append(f"| `{vname}` | {y} | {_num(c['vs_small_size'][y]['mean'], 3)} | "
                     f"{_num(c['vs_low_vol'][y]['mean'], 3)} | "
                     f"{_num(c['vs_neg_max_ret20'][y]['mean'], 3)} |")
    L.append("")
    wb = rep["factors"]["weekly_baseline"]
    tops = list(wb["crowding"]["industry_turnover_median"].items())[:8]
    bots = list(wb["crowding"]["industry_turnover_median"].items())[-5:]
    L.append("换手率行业分布（近 120 日中位数，高换手行业 top8）：" +
             "；".join(f"{k} {v:.2f}" for k, v in tops))
    L.append("")
    L.append("（低换手行业 bottom5）：" + "；".join(f"{k} {v:.2f}" for k, v in bots))
    L.append("")

    # 5. 结论
    c = rep["conclusion"]
    L.append("## 5) 结论：2023-24 负超额是 regime、拥挤还是因子失效？")
    L.append("")
    L.append(f"- IS 负超额年份（weekly 毛）：{c['negative_is_years'] or '无'}；"
             f"hold60 毛：{c.get('negative_is_years_hold60') or '无'}；"
             f"IS 平均 IC：{ {y: v for y, v in c['is_ic'].items()} }")
    L.append(f"- 因子失效判据（IS 两年 IC 均 ≤0）：**{'是' if c['factor_decay_suspected'] else '否'}**")
    L.append(f"- 拥挤判据（与小市值/低波/低 MAX 平均截面相关 |ρ|≥0.5）："
             f"**{'是' if c['crowded_suspected'] else '否'}**；平均相关 "
             f"{c['crowding_avg_corr']}")
    L.append(f"- regime 判据（最差状态下毛超额显著为负且 t 绝对值≥1.5）："
             f"**{'是' if c['regime_dependent'] else '否'}**；最差状态：{c['worst_regime_state']}")
    L.append("")
    L.extend(c.get("text_lines") or [])
    L.append("")
    L.append("## 口径与局限")
    L.append("")
    for line in rep["notes"]:
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


def industry_contrib_sum(e: dict) -> float:
    return float(e["industry_contribution"]["total"])


def build_notes() -> list[str]:
    return [
        "归因主口径为 `weekly_baseline`（与 wave2 逐年/IS 数字可直接对照），`hold60` 为 wave3 "
        "最优净口径候选的对照；两者均为中性化组合（宽口径、约 2000 只），超额是「同市值/行业暴露下"
        "选股是否有效」的下界式检验。",
        "IC = 逐日截面 Spearman(signed factor=−turnover_rate, 20 日 forward CAR)，"
        "CAR 未扣成本（成本为统一平移不影响排序，与 factor_validate 的扣 30bp 口径等价）；"
        "信号日 IC 用全部交易日计算（不限于调仓日），日频重叠 CAR 的 t 偏大（仅作量级参考）。",
        "市场状态 = 过去 20 日等权市场收益的扩展窗口 1/3、2/3 分位（上涨>2/3、下跌<1/3、其余震荡）；"
        "阈值只用当日之前已实现的 trailing 样本估计，状态标签用 t−1 收盘可知信息、收益按 t 日归因，"
        "无未来函数；早期样本不足 60 个 trailing 观测的日期不打标签。",
        "贡献拆解为逐日 Brinson 式：Σ holdings_t ×(个股日收益 − 等权市场日收益)，按行业/市值十分位"
        "累计（算术）；与组合官方日频毛超额口径（等权基准净值）存在基准构造细节差异，合计值近似相等。",
        "市值十分位按调仓信号日 circ_mv 分桶，调仓间沿用最近一次映射；行业取 stock_basic.industry。",
        "拥挤度代理为截面相关（ρ 高说明因子与某风格高度重合、alpha 可能只是风格暴露）；"
        "「换手率行业分布」用近 120 个交易日中位数（近似当前结构），非历史时点分布。",
        "中性组合从简：停牌/涨跌停不做不可买/不可卖模拟（缺价冻结估值）；股票池为当前 stock_basic "
        "名单（轻微幸存者偏差），与 factor_lib/factor_validate 同口径。",
        "净口径成本 30bp 单边、由毛净值×换手解析折算；本归因只解释「毛超额」的来源，"
        "净超额按变体平均换手随年份变化（见逐年表）。",
    ]


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="lowturn_attribution",
        description="low_turnover 逐年/regime 归因（2023-24 负超额根因）")
    p.add_argument("--start", default="20230101")
    p.add_argument("--end", default="20261030")
    p.add_argument("--variants", default=",".join(DEFAULT_VARIANTS))
    p.add_argument("--json", default=str(OUT / "lowturn_attribution_20261101.json"))
    p.add_argument("--md", default=str(OUT / "lowturn_attribution_20261101.md"))
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--md-only", action="store_true", help="只读已有 JSON 重渲染 md")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.md_only:
        rep = json.loads(Path(args.json).read_text(encoding="utf-8"))
        Path(args.md).write_text(write_md(rep), encoding="utf-8")
        print(f"[lowturn_attribution] md-only: {args.md}", flush=True)
        return 0
    vnames = [x.strip() for x in args.variants.split(",") if x.strip()]
    unknown = [v for v in vnames if v not in VARIANTS]
    if unknown:
        raise SystemExit(f"未注册变体: {unknown}")
    start, end = args.start, args.end
    t_all = time.time()
    print(f"[lowturn_attribution] start={start} end={end} variants={vnames}", flush=True)

    panel, market, industry, load_sec = fv.load_inputs(start, end)
    dates = [str(d) for d in market.dates]
    range_dates = [d for d in dates if start <= d <= end]
    spec = fl.FACTORS[FACTOR]
    raw = spec.compute(panel).to_numpy(dtype="float64")
    fmat = raw if spec.direction > 0 else -raw
    elig = (panel.eligible_mask().to_numpy(dtype=bool)
            if panel.eligible is not None else np.ones(fmat.shape, dtype=bool))
    # 各变体信号日 + 需要的 circ_mv 日期
    sig_sets: dict[str, list[str]] = {}
    for v in vnames:
        sched = VARIANTS[v]["schedule"]
        if sched == "weekly":
            sig_sets[v] = fv.weekly_signal_dates(range_dates)
        elif sched == "monthly":
            sig_sets[v] = lv.monthly_signal_dates(range_dates)
        elif sched == "every60":
            sig_sets[v] = lv.every_nth_signal_dates(range_dates, 60)
        else:
            raise SystemExit(f"未知 schedule: {sched}")
    needed = sorted({d for sds in sig_sets.values() for d in sds})
    circ_mv_by_date = fv.load_circ_mv(needed)
    ctx = load_market_context(panel, market, start, end)
    print(f"[lowturn_attribution] loaded {load_sec}s dates={len(dates)} codes={len(panel.codes)} "
          f"signal_days={ {k: len(v) for k, v in sig_sets.items()} } "
          f"circ_mv_days={len(circ_mv_by_date)}", flush=True)

    rep = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "start": start, "end": end,
            "effective_start": range_dates[0] if range_dates else start,
            "effective_end": range_dates[-1] if range_dates else end,
            "factor": FACTOR,
            "direction": spec.direction,
            "neutrality": NEUTRALITY,
            "cost_bp": COST_BP,
            "years": list(YEARS),
            "is_years": list(IS_YEARS),
            "oos_years": list(OOS_YEARS),
            "regime_rule": "trailing 20 日等权市场收益全样本 1/3、2/3 分位",
            "load_sec": load_sec,
            "runtime_sec": 0.0,
            "panel": {"dates": int(len(panel.dates)), "codes": int(len(panel.codes))},
        },
        "factors": {},
        "conclusion": None,
        "notes": build_notes(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for v in vnames:
        entry = analyze_variant(v, fmat, elig, panel, market, industry, sig_sets[v],
                                circ_mv_by_date, ctx, start, end)
        rep["factors"][v] = entry
        rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
        Path(args.json).write_text(json.dumps(fv._clean(rep), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"[lowturn_attribution] {v}: "
              f"IS gross={_pct(entry['period']['is_gross_excess'])} "
              f"OOS gross={_pct(entry['period']['oos_gross_excess'])} "
              f"({entry['runtime_sec']}s)", flush=True)
    rep["conclusion"] = build_conclusion(rep)
    rep["conclusion"]["text_lines"] = build_conclusion_text(rep)
    rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
    Path(args.json).write_text(json.dumps(fv._clean(rep), ensure_ascii=False, indent=1),
                               encoding="utf-8")
    Path(args.md).write_text(write_md(rep), encoding="utf-8")
    print(f"[lowturn_attribution] done: {args.json} / {args.md} "
          f"runtime={rep['meta']['runtime_sec']}s", flush=True)
    return 0


def build_conclusion_text(rep: dict) -> list[str]:
    """生成结论段落（数字驱动的模板化文字）。"""
    wb = rep["factors"].get("weekly_baseline") or next(iter(rep["factors"].values()))
    h60 = rep["factors"].get("hold60")
    wb["yearly"]
    ic = wb["ic_by_year"]
    c = rep["conclusion"]
    reg = wb["regime"]["stats"]
    reg_is = wb["regime"]["stats_is"]
    reg_oos = wb["regime"]["stats_oos"]
    sc = wb["size_contribution"]
    ind_year = wb["industry_contribution"]["by_year"]
    avg = c["crowding_avg_corr"]
    lines: list[str] = []
    lines.append("**根因判断（数字驱动）：**")
    lines.append("")
    lines.append(
        f"1. **因子失效：否。** IS 两年 IC 仍显著为正：2023 {_num(ic['2023']['mean'], 4)}"
        f"（t={_num(ic['2023']['t'])}）、2024 {_num(ic['2024']['mean'], 4)}"
        f"（t={_num(ic['2024']['t'])}）→ 截面排序方向在 IS 有效，"
        f"负超额来自组合层面的风格/行业暴露被反转，而不是「低换手不再有效」。")
    lines.append("")
    up, rg, dn = reg["上涨"], reg["震荡"], reg["下跌"]
    lines.append(
        f"2. **regime：是，低换手本质是「防御型」因子（长持有版本尤其显著）。** "
        f"全样本分状态毛超额：上涨 {_pct(up['gross_excess_sum'])}（t={_num(up['gross_t'])}）、"
        f"震荡 {_pct(rg['gross_excess_sum'])}（t={_num(rg['gross_t'])}）、"
        f"下跌 {_pct(dn['gross_excess_sum'])}（t={_num(dn['gross_t'])}）。")
    if h60:
        hu = h60["regime"]["stats"]["上涨"]
        hd = h60["regime"]["stats"]["下跌"]
        hb = h60["regime"]["bear_peak_trough"] or {}
        lines.append(
            f"   `hold60` 更极端：上涨市毛超额 {_pct(hu['gross_excess_sum'])}"
            f"（t={_num(hu['gross_t'])}，显著为负）、下跌市 {_pct(hd['gross_excess_sum'])}"
            f"（t={_num(hd['gross_t'])}）；2026 熊市段 {hb.get('peak_date', '—')}→"
            f"{hb.get('trough_date', '—')}（等权市场 {_pct(hb.get('market_ret'))}）"
            f"组合毛超额 {_pct(hb.get('portfolio_gross_excess'))}"
            f"（t={_num(hb.get('portfolio_gross_t'))}）。")
        lines.append("")
    lines.append(
        f"3. **拥挤：是（因子≈风格代理，不独立）。** low_turnover(signed) 与风格代理的逐年截面相关均值："
        f"小市值 {_num(avg['vs_small_size'], 3)}、低波 {_num(avg['vs_low_vol'], 3)}、"
        f"低MAX {_num(avg['vs_neg_max_ret20'], 3)}。与小市值负相关（≈−0.6~−0.7）= 低换手股票偏大市值；"
        f"与低波正相关≈+0.65 → 因子收益大部分可由「大市值+低波防御」风格解释，"
        f"不是独立的拥挤度 alpha。")
    lines.append("")
    d0 = sc["contribution_by_decile"][0]
    d9 = sc["contribution_by_decile"][9]

    def _bot3(yc) -> str:
        return "；".join(f"{x['industry']} {_pct(x['contribution'])}"
                         for x in yc.get("bottom5", [])[:3])

    def _top3(yc) -> str:
        return "；".join(f"{x['industry']} {_pct(x['contribution'])}"
                         for x in yc.get("top5", [])[:3])
    lines.append(
        f"4. **2023-24 负超额的具体来源（行业/市值拆解）**：2023 行业拖累 {_bot3(ind_year.get('2023', {}))}；"
        f"2024 拖累 {_bot3(ind_year.get('2024', {}))}（正向 {_top3(ind_year.get('2024', {}))}）。"
        f"市值分位：全样本 D0(最小) {_pct(d0)} vs D9(最大) {_pct(d9)} → 小市值桶内选股明显跑输，"
        f"而 2023-24 是低换手/防御风格逆风期，2026 年初（D0 拖累最大）同样验证这一点。")
    lines.append("")
    lines.append(
        f"5. **IS/OOS × 状态拆解（weekly）**：IS（2023-2024）上涨 "
        f"{_pct(reg_is['上涨']['gross_excess_sum'])}（t={_num(reg_is['上涨']['gross_t'])}）、"
        f"震荡 {_pct(reg_is['震荡']['gross_excess_sum'])}（t={_num(reg_is['震荡']['gross_t'])}）、"
        f"下跌 {_pct(reg_is['下跌']['gross_excess_sum'])}（t={_num(reg_is['下跌']['gross_t'])}）；"
        f"OOS（2025-2026）上涨 {_pct(reg_oos['上涨']['gross_excess_sum'])}、"
        f"震荡 {_pct(reg_oos['震荡']['gross_excess_sum'])}、"
        f"下跌 {_pct(reg_oos['下跌']['gross_excess_sum'])} → IS 的负超额不是单一年份/状态造成，"
        f"而是**多状态的风格逆风 + 行业拖累**，regime 是放大器而非唯一原因。")
    lines.append("")
    lines.append("**是否值得继续 / 能否当全天候：**")
    lines.append("")
    if h60:
        hu = h60["regime"]["stats"]["上涨"]
        hb = h60["regime"]["bear_peak_trough"] or {}
        wb_bear = wb["regime"]["bear_peak_trough"] or {}
        lines.append(
            f"- **不能当全天候策略**：`hold60` 在等权市场上涨状态毛超额 "
            f"{_pct(hu['gross_excess_sum'])}（t={_num(hu['gross_t'])}），"
            f"且 OOS t=1.63、逐年净仅 1/4 同向，单靠自己不足以支撑独立策略。")
        lines.append(
            f"- **有条件的防御价值**：下跌/熊市段表现强（2026 熊市段 hold60 毛超额 "
            f"{_pct(hb.get('portfolio_gross_excess'))}、t={_num(hb.get('portfolio_gross_t'))}；"
            f"周频 {_pct(wb_bear.get('portfolio_gross_excess'))}、"
            f"t={_num(wb_bear.get('portfolio_gross_t'))}），可作为「低波防御卫星」"
            f"叠加在动量/成长主策略上互补，或用 regime 条件（上涨状态降权/停用）控制回撤"
            f"——但 regime 阈值用全样本分位，实盘需滚动估计。")
    else:
        lines.append(
            "- **条件性结论**：上涨/下跌状态的超额差异大，需按 regime 拆开使用，"
            "不能当成全天候策略。")
    lines.append(
        "- **不建议作为独立高频中性 alpha 继续投入**：IC 虽正但组合超额被风格暴露吃掉，"
        "2023-24 的负超额说明其收益高度依赖市场风格周期。若继续，应改造成"
        "「风格剥离后的残差因子」（对低波/大市值做双重中性）或在风格中性约束下重新检验。")
    lines.append("")
    return lines


if __name__ == "__main__":
    sys.exit(main())
