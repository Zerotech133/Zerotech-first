# -*- coding: utf-8 -*-
"""支线 G：低频/成本优化验证（low_turnover 中性化组合）。

背景
----
wave2（tools/research/factor_validate.py → research/reports/factor_validate_20261101.md）：
30bp 单边 × 周频换手下所有候选因子净口径 fail（成本硬约束）；low_turnover 中性化
毛口径 OOS +9.40%（t=3.14）、逐年 3/4 同向，是唯一「有条件 pass」。
本脚本回答的是下一个问题：**把调仓频率降下来 / 加换手约束后，成本后是否可交易。**

4 类低频/降成本实现（全部叠加在「市值十分位 × 行业」市场权重法中性组合上）
------------------------------------------------------------------------
a) 月频调仓：每月最后一个交易日出信号，T+1 收盘建仓，持有到下月（约 20 个交易日）；
b) 60 日持有期：每 60 个交易日换仓（季度近似）；stagger 版 = 每 20 个交易日只换 1/3 仓，
   等效 3 段错开、每段持有 60 日的组合（降低单日冲击）；
c) 换手约束：桶内「买入阈值 top50% / 保留阈值 top60%（+10pp）」缓冲带（保留现有持仓优先）
   + 单次调仓双边换手上限 20%（超限按比例只换一部分）；
d) 成本敏感：10/20/30/50bp —— 由同一条毛净值路径 × 换手序列解析折算（成本不改变相对
   权重路径，故无需重跑），并在 30bp 上做主判定。

判定口径
--------
- 中性组合日频超额 = 组合日收益 − 等权全市场日收益（与 factor_validate 同口径）；
- OOS = 2025-2026，IS = 2023-2024；
- **pass_net**：净口径（对应成本）OOS 超额 > 0 且 OOS 日频超额 t ≥ 2 且逐年净超额同向 ≥ 3/4；
- 同时给毛口径判定（诊断 alpha 是否被成本吃掉）。

数据/口径
---------
quant_backtest/data/tushare：daily / adj_factor / daily_basic（20230103 起）/ stock_basic；
股票池 = factor_lib.tradable；T+1 收盘建仓；复权收益；缺价冻结估值（中性组合从简，
不做逐日涨跌停可买/可卖模拟——与 factor_validate 一致，已写入报告局限）。

产出
----
research/reports/lowturn_freq_validate_20261101.json / .md

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/research/lowturn_freq_validate.py
C:\\项目\\py312\\python.exe -X utf8 tools/research/lowturn_freq_validate.py --factors low_turnover
"""
from __future__ import annotations

import argparse
import json
import math
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

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SCHEMA_VERSION = "lowturn_freq_validate.v1"

CANDIDATES = ("low_turnover", "max_ret20", "vol20")
YEARS = ("2023", "2024", "2025", "2026")
IS_YEARS = ("2023", "2024")
OOS_YEARS = ("2025", "2026")

HORIZON = 20
NEUTRAL_HOLD_FRAC = 0.5       # 桶内持有因子分数最高的 50%（与 wave2 一致）
BUFFER_FRAC = 0.10            # 缓冲带：保留阈值放宽 +10pp（top50% 买 → top60% 保留）
MAX_TURNOVER = 0.20           # 单次调仓双边换手上限（20%）
COST_GRID_BP = (10, 20, 30, 50)
MAIN_COST_BP = 30.0
PARTICIPATION = 0.05
THRESHOLD_T = fv.THRESHOLD_T
MIN_POSITIVE_YEARS = fv.MIN_POSITIVE_YEARS

# 变体定义：schedule ∈ {weekly, monthly, every20, every60}
VARIANTS: dict[str, dict] = {
    "weekly_baseline": {
        "label": "周频全换仓", "schedule": "weekly", "tranche_frac": 1.0,
        "buffer_frac": 0.0, "max_turnover": None,
        "desc": "wave2 基线：每周换仓，无缓冲带/换手上限",
    },
    "monthly": {
        "label": "月频全换仓", "schedule": "monthly", "tranche_frac": 1.0,
        "buffer_frac": 0.0, "max_turnover": None,
        "desc": "每月末信号、T+1 建仓、持有约 20 个交易日",
    },
    "hold60": {
        "label": "60日持有(分段)", "schedule": "every60", "tranche_frac": 1.0,
        "buffer_frac": 0.0, "max_turnover": None,
        "desc": "每 60 个交易日整仓换仓（季度近似）",
    },
    "hold60_stagger": {
        "label": "60日持有(错开)", "schedule": "every20", "tranche_frac": 1.0 / 3.0,
        "buffer_frac": 0.0, "max_turnover": None,
        "desc": "每 20 个交易日只换 1/3 仓，3 段错开 → 每段持有 60 日、单日冲击降为 1/3",
    },
    "weekly_buffercap": {
        "label": "周频+缓冲带+换手上限", "schedule": "weekly", "tranche_frac": 1.0,
        "buffer_frac": BUFFER_FRAC, "max_turnover": MAX_TURNOVER,
        "desc": "周频，但 top50% 买入 / top60% 保留（缓冲带 +10pp）+ 单次双边换手 ≤20%",
    },
    "monthly_buffercap": {
        "label": "月频+缓冲带+换手上限", "schedule": "monthly", "tranche_frac": 1.0,
        "buffer_frac": BUFFER_FRAC, "max_turnover": MAX_TURNOVER,
        "desc": "月频 + 缓冲带 +10pp + 单次双边换手 ≤20%",
    },
}

DEFAULT_VARIANTS = tuple(VARIANTS)


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不依赖真实数据）
# --------------------------------------------------------------------------- #
def monthly_signal_dates(dates) -> list[str]:
    """每月最后一个交易日（信号日；T+1 建仓）。"""
    last: dict[str, str] = {}
    for d in sorted(str(x) for x in dates):
        last[d[:6]] = d
    return [last[k] for k in sorted(last)]


def every_nth_signal_dates(dates, n: int, offset: int = 0) -> list[str]:
    """每 n 个交易日取一个信号日（offset 为起点偏移，用于错开建仓的近似）。"""
    if n <= 0:
        raise ValueError(f"n 必须为正整数: {n!r}")
    ds = sorted(str(x) for x in dates)
    if not ds:
        return []
    return ds[int(offset)::int(n)]


def buffered_bucket_weights(scores: pd.Series, eligible: pd.Series,
                            circ_mv: pd.Series, industry: pd.Series,
                            prev_held=None,
                            hold_frac: float = NEUTRAL_HOLD_FRAC,
                            buffer_frac: float = 0.0) -> pd.Series:
    """市场权重法中性组合权重 + 缓冲带（保留现有持仓优先）。

    与 factor_validate.neutral_bucket_weights 同构，额外支持 buffer_frac：
      - 新买入：桶内分数排名 top(hold_frac)；
      - 保留：已有持仓只要仍在 top(hold_frac + buffer_frac) 就不卖（+10pp 缓冲带）；
      - 桶总权重仍 = 该桶全市场股票数占比，桶内等权。
    buffer_frac=0 且 prev_held 为空时退化为 wave2 同款权重。
    """
    empty = pd.Series(dtype="float64")
    if scores is None or circ_mv is None or len(circ_mv) == 0:
        return empty
    idx = pd.Index(circ_mv.index)
    s = pd.Series(scores).reindex(idx)
    e = pd.Series(eligible).reindex(idx).fillna(False).astype(bool)
    ind = pd.Series(industry).reindex(idx).fillna("未知").astype(str)
    mv = pd.Series(circ_mv).reindex(idx).astype("float64")
    market = mv.notna()
    if int(market.sum()) == 0:
        return empty
    size_dec = pd.qcut(mv[market], 10, labels=False, duplicates="drop")
    mkt = pd.DataFrame({"size_dec": size_dec, "ind": ind[market]})
    counts = mkt.groupby(["size_dec", "ind"], observed=True).size()
    if int(counts.sum()) == 0:
        return empty
    shares = counts / float(counts.sum())
    cand_mask = market & e & s.notna() & np.isfinite(s)
    cand = pd.DataFrame({
        "size_dec": size_dec.reindex(idx)[cand_mask],
        "ind": ind[cand_mask],
        "score": s[cand_mask].astype("float64"),
    })
    if cand.empty:
        return empty
    held = set(str(x) for x in (prev_held if prev_held is not None else []))
    cand = cand.sort_values(["size_dec", "score", "ind"],
                            ascending=[True, False, True], kind="stable")
    weights: dict[str, float] = {}
    covered_share = 0.0
    buf = max(0.0, float(buffer_frac))
    for (size_dec_v, ind_v), group in cand.groupby(["size_dec", "ind"], sort=False):
        key = (size_dec_v, ind_v)
        if key not in shares.index:
            continue
        w_b = float(shares.loc[key])
        if w_b <= 0:
            continue
        n_b = len(group)
        k_buy = max(1, int(math.ceil(hold_frac * n_b)))
        k_hold = max(k_buy, int(math.ceil((hold_frac + buf) * n_b)))
        codes = [str(c) for c in group.index]
        buy_top = set(codes[:k_buy])
        keep_top = set(codes[:k_hold])
        picked = [c for c in codes[:k_hold] if (c in buy_top or c in held)]
        if not picked:  # 理论不可达（k_buy≥1 保证 buy_top 非空）
            picked = codes[:1]
        per = w_b / float(len(picked))
        for code in picked:
            weights[code] = weights.get(code, 0.0) + per
        covered_share += w_b
    if not weights or covered_share <= 0:
        return empty
    scale = 1.0 / covered_share
    return pd.Series({k: v * scale for k, v in weights.items()},
                     dtype="float64").sort_index()


def rebalance_weights(drift: pd.Series, target: pd.Series,
                      tranche_frac: float = 1.0,
                      max_turnover: float | None = None) -> tuple[pd.Series, float]:
    """由「漂移权重」向「目标权重」调仓：分段换手 + 单次换手上限。

    - 空漂移（首次建仓）→ 直接买入目标，双边换手 = Σ目标权重（≈1）；
    - tranche_frac<1 → 只换 fence 比例的仓位：w = (1-f)·drift + f·target；
    - max_turnover → 双边换手 Σ|Δw| 超限时按 α = 上限/换手 按比例缩小交易。
    返回 (新权重, 双边换手)。
    """
    drift = pd.Series(drift if drift is not None else {}, dtype="float64")
    target = pd.Series(target if target is not None else {}, dtype="float64")
    target = target[target > 0]
    if target.empty:
        return pd.Series(dtype="float64"), 0.0
    if drift.empty:
        # 首次建仓：直接全额买入；换手上限只约束「再平衡」，不截断建仓
        # （否则未投资现金不入账，会让净值口径出现假的 -80% 首年）
        return target.sort_index(), float(target.sum())
    frac = float(np.clip(tranche_frac, 0.0, 1.0))
    if frac >= 1.0:
        final = target.copy()
    else:
        union_idx = drift.index.union(target.index)
        d = drift.reindex(union_idx, fill_value=0.0)
        t = target.reindex(union_idx, fill_value=0.0)
        final = (1.0 - frac) * d + frac * t
    union_idx = final.index.union(drift.index)
    d = drift.reindex(union_idx, fill_value=0.0)
    f = final.reindex(union_idx, fill_value=0.0)
    turnover = float(np.abs(f - d).sum())
    if max_turnover is not None and 0 < turnover > max_turnover:
        alpha = float(max_turnover) / turnover
        final = d + alpha * (f - d)
        turnover = float(max_turnover)
    final = final[final > 1e-12]
    return final.sort_index(), turnover


def net_nav_map(gross_nav_map: dict, turnover_rows, cost_bp: float) -> dict[str, float]:
    """由毛净值路径 + 换手序列解析折算任意费率的净净值。

    成本不改变相对权重路径（每次调仓权重按 NAV 归一），故：
      net(d) = gross(d) × Π_{调仓日 i ≤ d} (1 − turnover_i × cost_bp/10000)。
    """
    if cost_bp < 0:
        raise ValueError(f"cost_bp 不能为负: {cost_bp!r}")
    mp = cost_bp / 10_000.0
    cost_by_date = {str(row["date"]): float(row["turnover"]) * mp for row in turnover_rows}
    out: dict[str, float] = {}
    prod = 1.0
    for d in sorted(str(x) for x in gross_nav_map):
        if d in cost_by_date:
            prod *= (1.0 - cost_by_date[d])
        out[d] = float(gross_nav_map[d]) * prod
    return out


def cost_drag(gross_nav_map: dict, turnover_rows, cost_bp: float) -> float | None:
    """累计成本拖累（占毛净值比例）：1 − Π(1 − turnover_i × bp)。"""
    if not gross_nav_map:
        return None
    prod = 1.0
    mp = cost_bp / 10_000.0
    for row in turnover_rows:
        prod *= (1.0 - float(row["turnover"]) * mp)
    return round(float(1.0 - prod), 6)


def variant_metrics(gross_nav_map: dict, bench_nav: dict, turnover_rows,
                    cost_bp: float, adtv_yi: float | None,
                    avg_holdings: float | None,
                    years: tuple[str, ...] = YEARS,
                    oos_years: tuple[str, ...] = OOS_YEARS,
                    is_years: tuple[str, ...] = IS_YEARS) -> dict:
    """单变体单费率的完整指标：毛/净 OOS 超额与 t、逐年、换手、容量、判定。"""
    net_map = net_nav_map(gross_nav_map, turnover_rows, cost_bp)
    gross_series = fv.neutral_excess_series(gross_nav_map, bench_nav)
    net_series = fv.neutral_excess_series(net_map, bench_nav)

    def _block(series: pd.Series, ys: tuple[str, ...]) -> dict:
        dates = [d for d in series.index if fv.year_of(d) in ys]
        if not dates:
            return {"excess": None, "t": None, "n_days": 0}
        ex = float(series.loc[dates].sum())
        t = fv._tstat(series.loc[dates].to_numpy()).get("t")
        return {"excess": round(ex, 6), "t": round(t, 4) if t is not None else None,
                "n_days": len(dates)}

    annual_gross = fv.annual_excess_from_nav(gross_nav_map, bench_nav, years)
    annual_net = fv.annual_excess_from_nav(net_map, bench_nav, years)
    gross_oos = _block(gross_series, oos_years)
    net_oos = _block(net_series, oos_years)
    gross_is = _block(gross_series, is_years)
    net_is = _block(net_series, is_years)
    verdict_net = fv.judge_factor(net_oos["excess"], net_oos["t"], annual_net, years=years)
    verdict_gross = fv.judge_factor(gross_oos["excess"], gross_oos["t"], annual_gross, years=years)

    turns = [float(row["turnover"]) for row in turnover_rows]
    avg_turnover = round(float(np.mean(turns)), 4) if turns else None
    n_years = len(years)
    annual_turnover = round(float(np.sum(turns)) / n_years, 3) if turns else None
    rebalances_per_year = round(len(turns) / n_years, 2)
    hold = avg_holdings or 0
    capacity = fv.capacity_est_yi(adtv_yi * 1e8 if adtv_yi else None, hold, avg_turnover)
    return {
        "cost_bp": cost_bp,
        "gross_oos_excess": gross_oos["excess"],
        "gross_oos_t": gross_oos["t"],
        "net_oos_excess": net_oos["excess"],
        "net_oos_t": net_oos["t"],
        "gross_is_excess": gross_is["excess"],
        "gross_is_t": gross_is["t"],
        "net_is_excess": net_is["excess"],
        "net_is_t": net_is["t"],
        "annual_gross_excess": {y: (round(v, 6) if v is not None else None)
                                for y, v in annual_gross.items()},
        "annual_net_excess": {y: (round(v, 6) if v is not None else None)
                              for y, v in annual_net.items()},
        "annual_net_positive": verdict_net["positive_years"],
        "annual_net_n_years": verdict_net["n_years"],
        "avg_turnover": avg_turnover,
        "annual_turnover": annual_turnover,
        "rebalances_per_year": rebalances_per_year,
        "rebalance_count": len(turnover_rows),
        "avg_holdings": round(float(hold), 1) if avg_holdings else None,
        "cum_cost_drag": cost_drag(net_map or gross_nav_map, turnover_rows, cost_bp),
        "capacity_est_yi": capacity,
        "adtv_median_yi": round(float(adtv_yi), 4) if adtv_yi is not None else None,
        "verdict_net": verdict_net["verdict"],
        "verdict_net_reasons": verdict_net["reasons"],
        "verdict_gross": verdict_gross["verdict"],
        "verdict_gross_reasons": verdict_gross["reasons"],
    }


# --------------------------------------------------------------------------- #
# 中性组合低频回测（毛净值；成本由 net_nav_map 解析折算）
# --------------------------------------------------------------------------- #
def run_portfolio(market, fmat: np.ndarray, elig: np.ndarray, signal_dates,
                  circ_mv_by_date: dict, industry: pd.Series, start: str, end: str,
                  hold_frac: float = NEUTRAL_HOLD_FRAC,
                  buffer_frac: float = 0.0,
                  max_turnover: float | None = None,
                  tranche_frac: float = 1.0,
                  amt20: pd.DataFrame | None = None) -> dict:
    """低频中性组合回测（毛口径，T+1 收盘建仓、复权、缺价冻结）。

    返回毛净值路径 nav_map + 每次调仓的双边换手 turnover_rows；
    净口径由 net_nav_map 以任意成本折算（无需重跑）。
    """
    codes = list(market.codes)
    code_pos = {c: j for j, c in enumerate(codes)}
    start_pos = next((i for i, d in enumerate(market.dates) if d >= start), None)
    end_pos = next((i for i in range(len(market.dates) - 1, -1, -1)
                    if market.dates[i] <= end), None)
    if start_pos is None or end_pos is None or start_pos > end_pos:
        raise ValueError(f"区间 {start}~{end} 不在行情日期内")
    schedule: dict[str, str] = {}
    for sd in signal_dates:
        p = market.date_index(str(sd))
        if p is None or p + 1 >= len(market.dates):
            continue
        entry = market.dates[p + 1]
        if not (start <= entry <= end):
            continue
        schedule[entry] = str(sd)

    values: dict[int, float] = {}
    prev_target = pd.Series(dtype="float64")
    nav_rows: list[dict] = []
    turnover_rows: list[dict] = []
    holdings_list: list[int] = []
    adtv_list: list[float] = []

    for pos in range(start_pos, end_pos + 1):
        date = market.dates[pos]
        # 1) 持仓日收益（复权价；缺价冻结）
        if pos > start_pos and values:
            prev_px = market.adj[pos - 1]
            cur_px = market.adj[pos]
            for c in list(values):
                a, b = prev_px[c], cur_px[c]
                if np.isfinite(a) and np.isfinite(b) and a > 0:
                    values[c] *= float(b) / float(a)
        # 2) 调仓（信号日 T → T+1 收盘建仓）
        if date in schedule:
            sd = schedule[date]
            si = market.date_index(sd)
            target = pd.Series(dtype="float64")
            if si is not None:
                mv = circ_mv_by_date.get(sd)
                if mv is not None and len(mv):
                    target = buffered_bucket_weights(
                        scores=pd.Series(fmat[si], index=codes),
                        eligible=pd.Series(elig[si], index=codes),
                        circ_mv=mv, industry=industry,
                        prev_held=prev_target.index if len(prev_target) else None,
                        hold_frac=hold_frac, buffer_frac=buffer_frac,
                    )
            if len(target) > 0:
                nav_before = float(sum(values.values())) if values else 1.0
                drift = (pd.Series({codes[c]: v / nav_before for c, v in values.items()},
                                   dtype="float64")
                         if nav_before > 0 else pd.Series(dtype="float64"))
                final, turnover = rebalance_weights(
                    drift, target, tranche_frac=tranche_frac,
                    max_turnover=max_turnover,
                )
                if len(final) > 0:
                    values = {code_pos[c]: float(w) * nav_before
                              for c, w in final.items() if c in code_pos and w > 0}
                    prev_target = target
                    turnover_rows.append({"date": date, "signal_date": sd,
                                          "turnover": round(turnover, 6)})
                    holdings_list.append(len(values))
                    adtv = fv._adtv_median_yuan(amt20, sd, list(final.index))
                    if adtv:
                        adtv_list.append(adtv)
        nav = float(sum(values.values())) if values else 1.0
        nav_rows.append({"date": date, "nav": round(nav, 8), "n": len(values)})

    nav_map = {row["date"]: row["nav"] for row in nav_rows}
    return {
        "nav_map": nav_map,
        "turnover_rows": turnover_rows,
        "avg_turnover": round(float(np.mean([r["turnover"] for r in turnover_rows])), 4)
        if turnover_rows else None,
        "avg_holdings": round(float(np.mean(holdings_list)), 1) if holdings_list else None,
        "rebalance_count": len(turnover_rows),
        "adtv_median_yi": round(float(np.median(adtv_list)) / 1e8, 4) if adtv_list else None,
        "schedule_days": len(schedule),
    }


# --------------------------------------------------------------------------- #
# 单因子：全部变体 + 成本敏感
# --------------------------------------------------------------------------- #
def _signal_sets(range_dates, variants) -> dict[str, list[str]]:
    """按 schedule 类型生成信号日集合（一次生成、多变体复用）。"""
    out: dict[str, list[str]] = {}
    wanted = {VARIANTS[v]["schedule"] for v in variants}
    if "weekly" in wanted:
        out["weekly"] = fv.weekly_signal_dates(range_dates)
    if "monthly" in wanted:
        out["monthly"] = monthly_signal_dates(range_dates)
    if "every20" in wanted:
        out["every20"] = every_nth_signal_dates(range_dates, 20)
    if "every60" in wanted:
        out["every60"] = every_nth_signal_dates(range_dates, 60)
    return out


def validate_factor(name: str, panel, market, industry, bench_nav: dict,
                    range_dates: list[str], circ_mv_by_date: dict,
                    amt20: pd.DataFrame | None, start: str, end: str,
                    variants=DEFAULT_VARIANTS, cost_grid=COST_GRID_BP,
                    main_cost_bp: float = MAIN_COST_BP) -> dict:
    """对单个因子跑全部低频变体 + 成本敏感。"""
    t0 = time.time()
    spec = fl.FACTORS[name]
    raw = spec.compute(panel).to_numpy(dtype="float64")
    fmat = raw if spec.direction > 0 else -raw   # signed：越大越好
    elig = (panel.eligible_mask().to_numpy(dtype=bool)
            if panel.eligible is not None else np.ones(fmat.shape, dtype=bool))
    signal_sets = _signal_sets(range_dates, variants)
    out_variants: dict[str, dict] = {}
    for vname in variants:
        spec_v = VARIANTS[vname]
        sds = signal_sets[spec_v["schedule"]]
        run = run_portfolio(
            market, fmat, elig, sds, circ_mv_by_date, industry, start, end,
            hold_frac=NEUTRAL_HOLD_FRAC, buffer_frac=spec_v["buffer_frac"],
            max_turnover=spec_v["max_turnover"], tranche_frac=spec_v["tranche_frac"],
            amt20=amt20,
        )
        per_cost: dict[str, dict] = {}
        for bp in sorted(set(cost_grid) | {float(main_cost_bp)}):
            per_cost[str(int(bp))] = variant_metrics(
                run["nav_map"], bench_nav, run["turnover_rows"], float(bp),
                run["adtv_median_yi"], run["avg_holdings"],
            )
        main = per_cost[str(int(main_cost_bp))]
        entry = {
            "label": spec_v["label"],
            "desc": spec_v["desc"],
            "schedule": spec_v["schedule"],
            "tranche_frac": spec_v["tranche_frac"],
            "buffer_frac": spec_v["buffer_frac"],
            "max_turnover": spec_v["max_turnover"],
            "rebalance_count": run["rebalance_count"],
            "schedule_days": run["schedule_days"],
            "avg_turnover": main["avg_turnover"],
            "annual_turnover": main["annual_turnover"],
            "rebalances_per_year": main["rebalances_per_year"],
            "avg_holdings": main["avg_holdings"],
            "adtv_median_yi": main["adtv_median_yi"],
            "capacity_est_yi": main["capacity_est_yi"],
            "gross": {k: main[k] for k in ("gross_oos_excess", "gross_oos_t",
                                           "gross_is_excess", "gross_is_t")},
            "annual_gross_excess": main["annual_gross_excess"],
            "verdict_gross": main["verdict_gross"],
            "verdict_gross_reasons": main["verdict_gross_reasons"],
            "at_main_cost": main,
            "cost_sensitivity": per_cost,
            "net_oos_by_cost": {bp: per_cost[bp]["net_oos_excess"]
                                for bp in sorted(per_cost, key=int)},
        }
        out_variants[vname] = entry
        print(f"  [{name}] {vname:20s} gross_OOS={fv._pct(main['gross_oos_excess'])} "
              f"t={fv._fmt(main['gross_oos_t'], 2)} "
              f"net_OOS(30bp)={fv._pct(main['net_oos_excess'])} "
              f"turnover={main['avg_turnover']} reb/yr={main['rebalances_per_year']} "
              f"{main['verdict_net'].upper()}", flush=True)

    # 汇总该因子：哪些变体净口径可交易 / 净超额最优变体
    valid = [(v, e["at_main_cost"]) for v, e in out_variants.items()
             if e["at_main_cost"]["net_oos_excess"] is not None]
    pass_net = [v for v, m in valid if m["verdict_net"] == "pass"]
    best = max(valid, key=lambda kv: kv[1]["net_oos_excess"])[0] if valid else None
    results = {
        "name": name,
        "family": spec.family,
        "direction": spec.direction,
        "description": spec.description,
        "runtime_sec": round(time.time() - t0, 2),
        "variants": out_variants,
        "pass_net_variants": pass_net,
        "best_net_variant": best,
    }
    return results


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _cell(v, pct: bool = True, nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.{nd}f}%" if pct else f"{v:.{nd}f}"


def _verdict_icon(v: str) -> str:
    return "✅ **pass_net**" if v == "pass" else "❌ fail"


def write_md(rep: dict) -> str:
    meta = rep["meta"]
    L: list[str] = []
    L.append("# 低频/成本优化验证：low_turnover 中性化组合（月频/长持有/缓冲带/成本敏感）")
    L.append("")
    L.append(f"- 生成：{meta['generated_at']}；区间 {meta['start']}~{meta['end']}；"
             f"有效信号区间 {meta['effective_start']}~{meta['effective_end']}")
    L.append(f"- 中性化组合：市值十分位 × tushare 行业，市场权重法（桶总权重=市场股票数占比），"
             f"桶内按 signed 因子分数选 top{int(meta['hold_frac'] * 100)}% 等权；"
             f"T+1 收盘建仓、复权、缺价冻结；主成本 {meta['main_cost_bp']:.0f}bp 单边，"
             f"成本网格 {', '.join(str(int(x)) for x in meta['cost_grid_bp'])}bp")
    L.append(f"- 判定：**净口径 OOS（{meta['oos_years'][0]}-{meta['oos_years'][-1]}）超额 > 0 "
             f"且 OOS 日频超额 t ≥ {meta['threshold_t']:g} 且逐年净超额同向 ≥ "
             f"{meta['min_positive_years']}/4 → pass_net**（毛口径同阈值另列，用于诊断 alpha 是否被成本吃掉）")
    L.append(f"- 运行时：{meta['runtime_sec']:.1f}s（数据加载 {meta['load_sec']:.1f}s）；"
             f"因子 {len(rep['factors'])} 个；变体定义 {len(rep['variant_meta'])} 个"
             f"（控制因子只跑子集，见「口径与局限」）；"
             f"有效子集：{', '.join(meta['effective']['factors_effective']) or '无'}")
    L.append("")
    L.append("## 变体定义")
    L.append("")
    L.append("| 变体 | 频率/持有 | 缓冲带 | 单次换手上限 | 说明 |")
    L.append("|---|---|---|---|---|")
    for v, d in rep["variant_meta"].items():
        L.append(f"| `{v}` | {d['freq_desc']} | "
                 f"{('+' + str(int(d['buffer_frac'] * 100)) + 'pp') if d['buffer_frac'] else '—'} | "
                 f"{str(int(d['max_turnover'] * 100)) + '%' if d['max_turnover'] else '—'} | "
                 f"{d['desc']} |")
    L.append("")

    L.append("## 汇总表（主成本 30bp 单边；OOS=2025-2026，IS=2023-2024）")
    L.append("")
    L.append("| 因子 | 变体 | 换手/次(双边) | 调仓/年 | 毛 OOS 超额 | 毛 t | 净 OOS 超额 | 净 t | "
             "逐年净同向 | 累计成本拖累 | 容量(亿) | 判定(净) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for fname, e in rep["factors"].items():
        for vname, ve in e["variants"].items():
            m = ve["at_main_cost"]
            L.append(
                f"| {fname} | `{vname}` | {_cell(m['avg_turnover'], pct=False, nd=3)} | "
                f"{_cell(m['rebalances_per_year'], pct=False, nd=1)} | "
                f"{_cell(m['gross_oos_excess'])} | {_cell(m['gross_oos_t'], pct=False)} | "
                f"{_cell(m['net_oos_excess'])} | {_cell(m['net_oos_t'], pct=False)} | "
                f"{m['annual_net_positive']}/{m['annual_net_n_years']} | "
                f"{_cell(m['cum_cost_drag'])} | {_cell(m['capacity_est_yi'], pct=False, nd=1)} | "
                f"{_verdict_icon(m['verdict_net'])} |"
            )
    L.append("")

    L.append("## 成本敏感表（净 OOS 超额，按单边费率）")
    L.append("")
    header = "| 因子 | 变体 | " + " | ".join(f"{int(bp)}bp" for bp in meta["cost_grid_bp"]) + " |"
    sep = "|---|---|" + "---|" * len(meta["cost_grid_bp"])
    L.append(header)
    L.append(sep)
    for fname, e in rep["factors"].items():
        for vname, ve in e["variants"].items():
            cells = []
            for bp in meta["cost_grid_bp"]:
                val = ve["cost_sensitivity"].get(str(int(bp)), {}).get("net_oos_excess")
                cells.append(_cell(val))
            L.append(f"| {fname} | `{vname}` | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## 逐年净超额（30bp）")
    L.append("")
    L.append("| 因子 | 变体 | 2023 | 2024 | 2025 | 2026 | 净逐年同向 |")
    L.append("|---|---|---|---|---|---|---|")
    for fname, e in rep["factors"].items():
        for vname, ve in e["variants"].items():
            m = ve["at_main_cost"]
            n = m["annual_net_excess"]
            cells = [_cell(n.get(y)) for y in meta["years"]]
            L.append(f"| {fname} | `{vname}` | " + " | ".join(cells) + " | "
                     f"{m['annual_net_positive']}/{m['annual_net_n_years']} |")
    L.append("")

    L.append("## 结论：成本后是否可交易")
    L.append("")
    concl = rep.get("conclusion") or {}
    if concl.get("summary_text"):
        L.append(f"**总判定：{'成本后可交易（存在 pass_net 变体）' if concl.get('tradeable_after_cost') else '成本后仍不可交易（无 pass_net 变体）'}**")
        L.append("")
        L.append(concl["summary_text"])
        L.append("")
    for fname, e in rep["factors"].items():
        pn = e["pass_net_variants"]
        best = e["best_net_variant"]
        bm = e["variants"][best]["at_main_cost"] if best else None
        L.append(f"### {fname}")
        L.append("")
        if pn:
            L.append(f"- **pass_net 变体**：{', '.join('`' + x + '`' for x in pn)} → "
                     f"该实现下成本后可交易（净 OOS 超额>0、t≥2、逐年同向≥3/4）。")
        else:
            L.append("- **无 pass_net 变体** → 在已验证的降频/换手约束范围内，"
                     "成本后仍不可交易。")
        if bm is not None:
            L.append(f"- 最优净口径变体：`{best}`（{e['variants'][best]['label']}）——"
                     f"毛 OOS {_cell(bm['gross_oos_excess'])}（t={_cell(bm['gross_oos_t'], pct=False)}）、"
                     f"净 OOS {_cell(bm['net_oos_excess'])}（t={_cell(bm['net_oos_t'], pct=False)}）、"
                     f"平均双边换手 {_cell(bm['avg_turnover'], pct=False, nd=3)}、"
                     f"累计成本拖累 {_cell(bm['cum_cost_drag'])}、容量约 {_cell(bm['capacity_est_yi'], pct=False, nd=1)} 亿。")
            # 盈亏平衡费率：用 10-50bp 网格线性插值估算
            be = None
            xs, ys = [], []
            for bp in meta["cost_grid_bp"]:
                v = e["variants"][best]["cost_sensitivity"].get(str(int(bp)), {}).get("net_oos_excess")
                if v is not None:
                    xs.append(float(bp)); ys.append(float(v))
            if len(xs) >= 2 and ys[0] > 0 >= ys[-1]:
                for i in range(len(xs) - 1):
                    if ys[i] > 0 >= ys[i + 1]:
                        be = xs[i] + (0.0 - ys[i]) * (xs[i + 1] - xs[i]) / (ys[i + 1] - ys[i])
                        break
            if be is not None:
                L.append(f"- 盈亏平衡费率（净 OOS 超额=0，网格线性插值）：约 **{be:.1f}bp 单边**；"
                         f"实盘费率需低于该值才可能覆盖成本。")
            elif ys and all(y > 0 for y in ys):
                L.append(f"- 盈亏平衡费率：{meta['cost_grid_bp'][0]}~{meta['cost_grid_bp'][-1]}bp "
                         "网格内净 OOS 超额全程为正 → **成本已不构成约束**（不影响该变体的净口径结论）。")
            else:
                L.append("- 盈亏平衡费率：成本网格内净 OOS 超额未由正转负（或全程为负），"
                         "无法给出区间内平衡点。")
        L.append("")
    L.append("## 口径与局限")
    L.append("")
    for line in rep["notes"]:
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


def build_meta(start, end, load_sec, runtime_sec, panel, effective_factors,
               total_factors, variants) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "start": start,
        "end": end,
        "effective_start": str(panel.dates[0]) if len(panel.dates) else start,
        "effective_end": str(panel.dates[-1]) if len(panel.dates) else end,
        "load_sec": load_sec,
        "runtime_sec": round(runtime_sec, 2),
        "panel": {"dates": int(len(panel.dates)), "codes": int(len(panel.codes))},
        "years": list(YEARS),
        "is_years": list(IS_YEARS),
        "oos_years": list(OOS_YEARS),
        "hold_frac": NEUTRAL_HOLD_FRAC,
        "buffer_frac": BUFFER_FRAC,
        "max_turnover": MAX_TURNOVER,
        "main_cost_bp": MAIN_COST_BP,
        "cost_grid_bp": list(COST_GRID_BP),
        "threshold_t": THRESHOLD_T,
        "min_positive_years": MIN_POSITIVE_YEARS,
        "universe": "factor_lib.tradable",
        "execution": "T+1 收盘建仓；复权收益；缺价冻结估值；不做逐日涨跌停可买/可卖模拟",
        "cost_model": "两腿换手 Σ|Δw| × bp（Σ|Δw|=1 表示买入 100% 仓位一次单边成本 bp）",
        "total_factors": total_factors,
        "effective": {"factors_effective": effective_factors},
        "variant_meta": variants,
    }


def build_notes() -> list[str]:
    return [
        "中性组合为宽口径（每个市值×行业桶都持有高分股，约 2000 只），alpha 被桶内分散稀释；"
        "其超额是「同市值/行业暴露下选股是否有效」的下界式检验，不等于可交易的 top50 组合收益。",
        "成本由同一条毛净值路径 × 换手序列解析折算：成本不改变相对权重路径（每次调仓按 NAV 归一），"
        "故 net(d)=gross(d)×Π(1−turnover_i×bp)。10/20/30/50bp 共用一次回测，无重复模拟误差。",
        "换手口径为两次调仓之间 Σ|w_target−w_drift|（双边）；30bp 单边 → 年化成本 ≈ 年化双边换手×30bp。"
        "首日建仓换手≈1（买入一次）计入成本但不在 OOS 内，对判定无实质影响。",
        "60 日持有两版：`hold60` 为每 60 个交易日整仓换仓（分段近似）；"
        "`hold60_stagger` 为每 20 个交易日只换 1/3 仓（3 段错开），单日冲击更小、换手总量相同量级。",
        "缓冲带实现：桶内 top50% 买入、已有持仓仍处 top60%（+10pp）则保留，"
        "超限部分按 α=上限/换手 比例只换一部分；桶总权重仍等于市场股票数占比，行业/市值中性不被破坏。",
        "中性组合从简：停牌/涨跌停不做不可买/不可卖模拟（缺价冻结估值），"
        "相对 bt_harness 原始组合偏乐观；但本报告只做同一中性口径下的变体横向比较，结论不受该偏置的差分影响。",
        "T+1 收盘建仓（信号日次一交易日），与 factor_validate/bt_harness 同口径；"
        "月频信号=每月最后一个交易日，60 日/20 日信号为交易日等距切片（非自然月）。",
        "股票池为当前 stock_basic 名单 + 上市日期，存在轻微幸存者偏差（与 factor_lib/bt_harness 同口径）。",
        "容量估算：单日单票成交额 ≤ ADTV 的 5%，AUM_max ≈ 5%×ADTV×持股数/(双边换手/2)；"
        "未考虑冲击成本与拥挤度非线性，30bp 费率本身也已假设自有资金/低冲击。",
        "判定阈值沿用 wave2：OOS 超额>0 且 t≥2 且逐年同向≥3/4；本次主判定用**净口径**（回答成本后是否可交易），"
        "毛口径判定仅作诊断（回答 alpha 是否被成本吃掉）。样本内/外为简单切分（IS=2023-24，OOS=2025-26），"
        "非严格 walk-forward 参数选择。",
    ]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="lowturn_freq_validate",
        description="低频/成本优化验证：low_turnover 中性化组合（月频/长持有/缓冲带/成本敏感）")
    p.add_argument("--start", default="20230101")
    p.add_argument("--end", default="20261030")
    p.add_argument("--factors", default=",".join(CANDIDATES),
                   help="逗号分隔因子名（默认 low_turnover,max_ret20,vol20）")
    p.add_argument("--variants", default=",".join(DEFAULT_VARIANTS),
                   help="逗号分隔变体名（默认全部）")
    p.add_argument("--json", default=str(OUT / "lowturn_freq_validate_20261101.json"))
    p.add_argument("--md", default=str(OUT / "lowturn_freq_validate_20261101.md"))
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--md-only", action="store_true",
                   help="只读已有 JSON 重渲染 md（不跑数据）")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.md_only:
        rep = json.loads(Path(args.json).read_text(encoding="utf-8"))
        Path(args.md).write_text(write_md(rep), encoding="utf-8")
        print(f"[lowturn_freq_validate] md-only: {args.md}", flush=True)
        return 0
    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    vnames = [x.strip() for x in args.variants.split(",") if x.strip()]
    unknown_f = [n for n in names if n not in fl.FACTORS]
    unknown_v = [v for v in vnames if v not in VARIANTS]
    if unknown_f:
        raise SystemExit(f"未注册因子: {unknown_f}")
    if unknown_v:
        raise SystemExit(f"未注册变体: {unknown_v}")
    start, end = args.start, args.end
    print(f"[lowturn_freq_validate] start={start} end={end} factors={names} "
          f"variants={vnames}", flush=True)

    t_all = time.time()
    panel, market, industry, load_sec = fv.load_inputs(start, end)
    range_dates = [str(d) for d in panel.dates if start <= str(d) <= end]
    # 所有变体需要的信号日 union（circ_mv 只加载需要的日期）
    signal_sets = _signal_sets(range_dates, vnames)
    needed = sorted({d for sds in signal_sets.values() for d in sds})
    circ_mv_by_date = fv.load_circ_mv(needed)
    bench_nav = fv._bench_nav_map(market, start, end)
    amt20 = panel.amount.rolling(20, min_periods=5).mean() if "amount" in panel.fields else None
    print(f"[lowturn_freq_validate] panel loaded {load_sec}s "
          f"dates={len(panel.dates)} codes={len(panel.codes)} "
          f"signal_days={ {k: len(v) for k, v in signal_sets.items()} } "
          f"circ_mv_days={len(circ_mv_by_date)}", flush=True)

    variant_meta = {
        v: {
            "label": VARIANTS[v]["label"],
            "freq_desc": {"weekly": "周频", "monthly": "月频",
                          "every20": "每20日换1/3仓(持有60日)",
                          "every60": "每60交易日"}[VARIANTS[v]["schedule"]],
            "buffer_frac": VARIANTS[v]["buffer_frac"],
            "max_turnover": VARIANTS[v]["max_turnover"],
            "desc": VARIANTS[v]["desc"],
        }
        for v in vnames
    }
    rep = {
        "meta": build_meta(start, end, load_sec, 0.0, panel, [], len(names), variant_meta),
        "variant_meta": variant_meta,
        "factors": {},
        "summary": None,
        "conclusion": None,
        "notes": build_notes(),
    }
    if range_dates:
        rep["meta"]["effective_start"] = range_dates[0]
        rep["meta"]["effective_end"] = range_dates[-1]
    OUT.mkdir(parents=True, exist_ok=True)
    effective: list[str] = []
    for name in names:
        try:
            entry = validate_factor(
                name, panel, market, industry, bench_nav, range_dates,
                circ_mv_by_date, amt20, start, end, variants=vnames,
            )
        except Exception as exc:  # 单因子失败不拖垮整体
            import traceback
            traceback.print_exc()
            rep.setdefault("errors", []).append({"factor": name, "error": repr(exc)})
            print(f"[lowturn_freq_validate] {name} FAILED: {exc!r}", flush=True)
            continue
        rep["factors"][name] = entry
        effective.append(name)
        rep["meta"]["effective"]["factors_effective"] = effective
        rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
        Path(args.json).write_text(json.dumps(fv._clean(rep), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"[lowturn_freq_validate] {name}: pass_net={entry['pass_net_variants']} "
              f"best_net={entry['best_net_variant']} ({entry['runtime_sec']}s)", flush=True)
    # 汇总/结论
    rep["summary"] = {
        "factors": effective,
        "pass_net_by_factor": {n: rep["factors"][n]["pass_net_variants"]
                               for n in effective},
        "best_net_variant": {n: rep["factors"][n]["best_net_variant"]
                             for n in effective},
        "any_pass_net": any(rep["factors"][n]["pass_net_variants"] for n in effective),
    }
    L = []
    for n in effective:
        pn = rep["factors"][n]["pass_net_variants"]
        best = rep["factors"][n]["best_net_variant"]
        bm = rep["factors"][n]["variants"][best]["at_main_cost"] if best else None
        L.append({
            "factor": n,
            "pass_net_variants": pn,
            "best_net_variant": best,
            "best_net_oos_excess": bm["net_oos_excess"] if bm else None,
            "best_net_oos_t": bm["net_oos_t"] if bm else None,
            "tradeable_after_cost": bool(pn),
        })
    rep["conclusion"] = {
        "main_cost_bp": MAIN_COST_BP,
        "tradeable_after_cost": bool(any(x["tradeable_after_cost"] for x in L)),
        "by_factor": L,
    }
    rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
    Path(args.json).write_text(json.dumps(fv._clean(rep), ensure_ascii=False, indent=1),
                               encoding="utf-8")
    Path(args.md).write_text(write_md(rep), encoding="utf-8")
    print(f"[lowturn_freq_validate] done: {args.json} / {args.md} "
          f"runtime={rep['meta']['runtime_sec']}s "
          f"tradeable_after_cost={rep['conclusion']['tradeable_after_cost']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
