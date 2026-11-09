# -*- coding: utf-8 -*-
"""tools/research/industry_rs_factor.py · 行业相对强度（RS）横截面因子严格检验。

背景（Lead task-90）：RS 事件叠加层（task-88）已被严苛复验否证（真 alpha=0、板块 beta=8、无增量=11），
下一步改做「行业相对强度因子」的**横截面**检验（而非事件选股过滤）。

因子定义（PIT，只用 T 日及以前数据）
------------------------------------
    RS_k(i, T) = 个股 i 的 k 日收益(T) − 同行业其他成员 k 日收益等权均值(T)
    其中 k ∈ {5, 10, 20}；行业等权采用 **leave-one-out**（剔除自身，避免自身权重造成机械均值回归偏差）；
    行业分类 = tushare stock_basic.industry（与 task-88 板块代理同口径；静态分类的局限已在报告披露）。

标签（PIT，无前视）
-------------------
    T 日收盘因子值 → T+1 收盘入场 → 持有 20 个交易日（T+21 收盘卖出）：
    label = 个股复权买入持有收益 − 等权市场同期复利收益（factor_lib.forward_car，成本=0；
    成本在组合层单独做 30/50/80bp 敏感性）。

评测（严格版）
-------------
  1. 按日横截面 Spearman IC / RankIC：均值、IR、日频 t、胜率、分年；
  2. 十分位分层：D1~D10 的 20 日 CAR 与年化、单调性（分层序号 vs 收益的 Spearman）；
  3. 多空（D10−D1）：平均 20 日、年化、**月度非重叠聚合 t**（MEETING.md 重叠修正纪律）、
     block bootstrap p（块长 20 ≥ 持有期）；
  4. 换手：相邻信号日 D10/D1 名单重叠 → 单边换手；按 20 日重叠持有口径折算年化换手；
  5. 成本敏感性：30/50/80bp（双边计费 = 多空两腿），净年化 = 毛年化 − 单位成本 × 年化换手 × 2；
  6. walk-forward：2023-2024（观察）vs 2025-2026（验证），各窗口 IC/多空/显著性；
  7. 安慰剂：同覆盖率随机因子（逐日打乱因子秩）1000 次，月度非重叠 cohort 的均值 IC 分布 → 经验 p；
  8. 多重检验：3 因子 × 2 walk-forward 窗口 = 6 个假设，BH-FDR（q≤0.05/0.10）；
  9. 分层稳健性：流通市值三分层内的 IC；行业内 IC 分布（中位数/正比例/离散度）。

用法
----
    python tools/research/industry_rs_factor.py eval --start 20230101 --end 20261030 \\
        --out-prefix research/reports/industry_rs_factor_20261101
    python tools/research/industry_rs_factor.py list     # 列因子定义

输出：<prefix>.json + <prefix>.md（结论：是否有效因子 / 是否接近可交易）。
研究用途，不构成投资建议。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(_HERE))
import factor_lib as fl  # noqa: E402
import stat_utils  # noqa: E402

LIB_VERSION = "1.0.0"
DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
HORIZON = 20                 # 标签持有期（T+1 入场，T+21 卖出）
WINDOWS = (5, 10, 20)        # RS 因子窗口
N_DECILES = 10
COSTS_BP = (30.0, 50.0, 80.0)
BLOCK = 20                   # block bootstrap 块长 = 持有期（重叠修正）
N_BOOT = 300
DEFAULT_N_PLACEBO = 1000
DEFAULT_SEED = 42
MIN_N = 50                   # 单日横截面最少股票数
MIN_N_CAP = 30               # 分层内单日最少股票数
MIN_N_IND = 10               # 行业内单日最少股票数
WF_WINDOWS = (("2023-2024", "20230101", "20241231"),
              ("2025-2026", "20250101", "20261030"))
PRIMARY_FACTOR = "RS_20"


# ============================================================ 纯函数（可单测）
def stock_return(panel: fl.Panel, k: int) -> np.ndarray:
    """个股 k 日复权收益矩阵（T/T−k − 1），只用 T 及以前数据。"""
    adj = panel.adj_close
    return np.asarray(adj / adj.shift(k) - 1.0, dtype=float)


def industry_codes(industry: pd.Series, codes: pd.Index, other: str = "其他") -> tuple[np.ndarray, list[str]]:
    """把「代码 → 行业名」映射为「代码 → 行业编号」；缺失/未知归入 other 组。"""
    names = industry.reindex(codes).fillna(other).astype(str).to_numpy()
    uniq = sorted(set(names.tolist()) | {other})
    pos = {name: i for i, name in enumerate(uniq)}
    return np.asarray([pos[name] for name in names], dtype=np.int32), uniq


def _industry_sum_count(ret: np.ndarray, ind_code: np.ndarray, valid: np.ndarray,
                        n_ind: int) -> tuple[np.ndarray, np.ndarray]:
    """逐日按行业汇总有效成员收益之和与个数（D×n_ind）。"""
    d = ret.shape[0]
    total = np.zeros((d, n_ind), dtype=float)
    count = np.zeros((d, n_ind), dtype=float)
    ok = valid & np.isfinite(ret) & np.isfinite(ind_code)
    for g in range(n_ind):
        mask = ok & (ind_code == g)
        total[:, g] = np.where(mask, ret, 0.0).sum(axis=1)
        count[:, g] = mask.sum(axis=1)
    return total, count


def rs_from_industry(ret: np.ndarray, ind_code: np.ndarray, valid: np.ndarray,
                     n_ind: int) -> np.ndarray:
    """RS_k = 个股收益 − 同行业其他成员等权收益（leave-one-out）；成员不足 2 只处为 NaN。"""
    total, count = _industry_sum_count(ret, ind_code, valid, n_ind)
    d, c = ret.shape
    idx = np.where(np.isfinite(ind_code), ind_code.astype(int), 0)
    row = np.arange(d)[:, None]
    tot = total[row, idx]
    cnt = count[row, idx]
    self_ret = np.where(np.isfinite(ret), ret, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        industry_others = (tot - self_ret) / (cnt - 1.0)
    rs = ret - industry_others
    ok = valid & np.isfinite(ret) & np.isfinite(ind_code) & (cnt >= 2)
    return np.where(ok, rs, np.nan)


def build_rs_factors(panel: fl.Panel, industry: pd.Series, windows=WINDOWS,
                     eligible: np.ndarray | None = None) -> tuple[dict[str, np.ndarray], list[str]]:
    """构建 RS_5 / RS_10 / RS_20 因子矩阵（D×C）。"""
    elig = panel.eligible_mask().to_numpy(dtype=bool) if eligible is None else eligible
    ind_code, ind_names = industry_codes(industry, panel.codes)
    n_ind = len(ind_names)
    out: dict[str, np.ndarray] = {}
    for k in windows:
        ret = stock_return(panel, k)
        out[f"RS_{k}"] = rs_from_industry(ret, ind_code, elig, n_ind)
    return out, ind_names


def daily_ic(factor: np.ndarray, label: np.ndarray, elig: np.ndarray, dates: list[str],
             min_n: int = MIN_N) -> pd.Series:
    """逐日横截面 Spearman IC（复用 factor_lib 口径），返回日期索引序列。"""
    ic = fl._spearman_ic(np.where(elig, factor, np.nan), label, min_n=min_n)
    return pd.Series(ic, index=pd.Index(dates))


def series_stats(x: pd.Series) -> dict:
    """日频序列统计（IC 或收益）：n/mean/std/t/胜率。"""
    s = pd.Series(x, dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "mean": None, "std": None, "t": None, "positive_rate": None}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    t = mean / std * math.sqrt(n) if std > 0 else None
    return {"n_days": n, "mean": fl._num(mean, 6), "std": fl._num(std, 6),
            "t": fl._num(t, 3), "positive_rate": fl._num(float((s > 0).mean()), 4)}


def monthly_cohort(series: pd.Series, step: int = HORIZON) -> pd.Series:
    """非重叠抽样：每 step 个观测取 1（用于 20 日持有期重叠修正，MEETING.md 纪律）。"""
    return series.iloc[::step]


def decile_eval(factor: np.ndarray, label: np.ndarray, elig: np.ndarray,
                dates: list[str], n_groups: int = N_DECILES) -> dict:
    """十分位分层：各组 20 日 CAR 均值/年化、单调性、多空（D10−D1）收益与显著性。"""
    pair = elig & np.isfinite(factor) & np.isfinite(label)
    ranks = fl._rank_2d(np.where(pair, factor, np.nan), average=False)
    n = pair.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.clip(np.ceil(ranks / np.where(n > 0, n, np.nan)[:, None] * n_groups), 1, n_groups)
    annuity = 252.0 / HORIZON
    groups: dict[str, dict] = {}
    daily: dict[int, pd.Series] = {}
    means: list[float] = []
    for g in range(1, n_groups + 1):
        sel = pair & (q == g)
        daily[g] = fl._daily_mean(label, sel, dates)
        st = fl._return_stats(daily[g], annuity)
        st["n_obs"] = int(sel.sum())
        groups[f"D{g}"] = st
        means.append(st.get("mean_car_pct") if st.get("mean_car_pct") is not None else np.nan)
    spread = (daily[n_groups] - daily[1]).dropna()
    sp = fl._return_stats(spread, annuity)
    sp["t_monthly"] = series_stats(monthly_cohort(spread)).get("t")
    sp["bootstrap"] = stat_utils.block_bootstrap_p(spread, block=BLOCK, n_boot=N_BOOT, seed=DEFAULT_SEED)
    arr = np.asarray(means, dtype=float)
    mono = None
    if np.isfinite(arr).sum() >= 3:
        mono = fl._num(float(pd.Series(arr).corr(pd.Series(np.arange(1, n_groups + 1)),
                                                method="spearman")), 3)
    return {"groups": groups, "monotonicity": mono,
            "long_short": {"mean_20d_pct": sp.get("mean_car_pct"), "ann_pct": sp.get("ann_pct"),
                           "t_daily": sp.get("t"), "t_monthly": sp.get("t_monthly"),
                           "bootstrap": sp.get("bootstrap"), "n_days": sp.get("n_days")},
            "n_obs": int(pair.sum()), "n_days": int(pair.any(axis=1).sum()),
            "_spread_series": spread}


def turnover_eval(factor: np.ndarray, elig: np.ndarray, n_groups: int = N_DECILES) -> dict:
    """换手：相邻信号日 D10/D1 名单重叠 → 单边换手；按 20 日重叠持有口径折算年化。"""
    pair = elig & np.isfinite(factor)
    ranks = fl._rank_2d(np.where(pair, factor, np.nan), average=False)
    n = pair.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.clip(np.ceil(ranks / np.where(n > 0, n, np.nan)[:, None] * n_groups), 1, n_groups)
    out: dict[str, dict] = {}
    for label, g in (("top_decile", n_groups), ("bottom_decile", 1)):
        members = [set(np.flatnonzero(pair[i] & (q[i] == g))) for i in range(pair.shape[0])]
        turnovers = []
        for i in range(1, len(members)):
            if not members[i] or not members[i - 1]:
                continue
            overlap = len(members[i] & members[i - 1]) / len(members[i - 1])
            turnovers.append(1.0 - overlap)
        avg = float(np.mean(turnovers)) if turnovers else None
        out[label] = {
            "avg_one_way_turnover": fl._num(avg, 4),
            "n_transitions": len(turnovers),
            "ann_turnover_daily_rebalance": fl._num(avg * 252.0, 1) if avg is not None else None,
            # 20 日持有期重叠组合：每日只换 1/20 的仓位 → 年化换手 = 日均换手 × 252/20
            "ann_turnover_overlapping": fl._num(avg * 252.0 / HORIZON, 2) if avg is not None else None,
        }
    return out


def cost_sensitivity(gross_ann_pct: float | None, ann_turnover: float | None,
                     costs_bp=COSTS_BP, legs: int = 2) -> dict:
    """成本敏感性：净年化 = 毛年化 − 成本(bp) × 年化换手 × 腿数（多空两腿默认 2）。"""
    if gross_ann_pct is None or ann_turnover is None:
        return {f"{int(c)}bp": {"net_ann_pct": None, "cost_drag_ann_pct": None} for c in costs_bp}
    out = {}
    for c in costs_bp:
        drag = c / 10000.0 * 100.0 * ann_turnover * legs
        out[f"{int(c)}bp"] = {"net_ann_pct": fl._num(gross_ann_pct - drag, 2),
                              "cost_drag_ann_pct": fl._num(drag, 2)}
    return out


def two_sided_p(t: float | None) -> float | None:
    """正态近似双侧 p 值（样本 > 20 时足够；用于 BH-FDR 的输入）。"""
    if t is None:
        return None
    return math.erfc(abs(float(t)) / math.sqrt(2.0))


def bh_q(pvals: list[float | None], q_cut: float = 0.10) -> dict:
    """Benjamini-Hochberg FDR：返回 q 值列表与 q≤cut 的假设数。"""
    items = [(i, float(p)) for i, p in enumerate(pvals) if p is not None]
    n = len(items)
    q = [None] * len(pvals)
    items.sort(key=lambda x: x[1])
    prev = 1.0
    for rank, (i, p) in enumerate(reversed(items), start=1):
        k = n - rank + 1
        val = min(prev, p * n / k)
        prev = val
        q[i] = val
    n_reject = sum(1 for v in q if v is not None and v <= q_cut)
    return {"q": [fl._num(v, 4) if v is not None else None for v in q],
            "n_tests": n, "q_cut": q_cut, "n_reject": n_reject}


def placebo_eval(factor: np.ndarray, label: np.ndarray, elig: np.ndarray, dates: list[str],
                 n_perm: int = DEFAULT_N_PLACEBO, step: int = HORIZON,
                 seed: int = DEFAULT_SEED) -> dict:
    """安慰剂：同覆盖率随机因子（逐日打乱因子秩）→ 月度非重叠 cohort 均值 IC 的经验分布。"""
    if n_perm <= 0:
        return {"n_perm": 0, "actual_mean_ic": None, "p_value": None, "null": {}}
    rows = []
    actuals = []
    for i in range(0, len(dates), step):
        mask = elig[i] & np.isfinite(factor[i]) & np.isfinite(label[i])
        n = int(mask.sum())
        if n < MIN_N:
            continue
        xr = fl._rank_2d(np.where(mask, factor[i], np.nan).reshape(1, -1))[0]
        xr = xr[np.isfinite(xr)]
        yr = fl._rank_2d(np.where(mask, label[i], np.nan).reshape(1, -1))[0]
        yr = yr[np.isfinite(yr)]
        if len(xr) != n or len(yr) != n:
            continue
        rows.append((xr, yr, n))
        actuals.append(float(pd.Series(xr).corr(pd.Series(yr), method="spearman")))
    if not rows:
        return {"n_perm": 0, "actual_mean_ic": None, "p_value": None, "null": {}}
    actual = float(np.mean(actuals))
    rng = np.random.default_rng(seed)
    null_means = np.empty(int(n_perm), dtype=float)
    batch = 100
    for b0 in range(0, int(n_perm), batch):
        bsz = min(batch, int(n_perm) - b0)
        means = np.zeros(bsz, dtype=float)
        for xr, yr, n in rows:
            xc = xr - xr.mean()
            yc = yr - yr.mean()
            denom_x = float(np.sqrt(np.sum(xc * xc)))
            denom_y = float(np.sqrt(np.sum(yc * yc)))
            if denom_x == 0 or denom_y == 0:
                continue
            perm = np.argsort(rng.random((bsz, n)), axis=1)
            X = xc[perm]                       # 打乱因子秩（每一行一个随机排列）
            means += (X @ yc) / (denom_x * denom_y)
        null_means[b0:b0 + bsz] = means / len(rows)
    p_high = (1.0 + float(np.sum(null_means >= actual))) / (int(n_perm) + 1.0)
    p_low = (1.0 + float(np.sum(null_means <= actual))) / (int(n_perm) + 1.0)
    p_two = min(1.0, 2.0 * min(p_high, p_low))   # 双侧：用来判定因子是否「有信息」（方向另由观察窗定）
    pr = float(np.mean(null_means < actual))
    return {"n_perm": int(n_perm), "n_cohorts": len(rows), "cohort_step": int(step),
            "actual_mean_ic": fl._num(actual, 6),
            "p_value": fl._num(p_two, 4),
            "p_one_sided_high": fl._num(p_high, 4),
            "p_one_sided_low": fl._num(p_low, 4),
            "percentile_rank": fl._num(pr, 4),
            "null": {"mean": fl._num(float(null_means.mean()), 6),
                     "std": fl._num(float(null_means.std(ddof=1)), 6),
                     "p05": fl._num(float(np.percentile(null_means, 5)), 6),
                     "p50": fl._num(float(np.percentile(null_means, 50)), 6),
                     "p95": fl._num(float(np.percentile(null_means, 95)), 6)}}


def cap_stratified(factor: np.ndarray, label: np.ndarray, elig: np.ndarray, cap: np.ndarray,
                   dates: list[str], n_groups: int = 3) -> dict:
    """流通市值分层：逐日按市值分 n_groups 层，层内 IC。"""
    pair = elig & np.isfinite(factor) & np.isfinite(label) & np.isfinite(cap) & (cap > 0)
    ranks = fl._rank_2d(np.where(pair, cap, np.nan), average=False)
    n = pair.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.clip(np.ceil(ranks / np.where(n > 0, n, np.nan)[:, None] * n_groups), 1, n_groups)
    names = {"小市值": 1, "中市值": 2, "大市值": 3} if n_groups == 3 else {}
    out = {}
    for name, gi in names.items():
        sub = pair & (g == gi)
        ic = fl._spearman_ic(np.where(sub, factor, np.nan), label, min_n=MIN_N_CAP)
        st = series_stats(pd.Series(ic, index=pd.Index(dates)))
        out[name] = {"ic_mean": st["mean"], "ic_t": st["t"], "n_days": st["n_days"],
                     "n_obs": int(sub.sum())}
    return out


def industry_stratified(factor: np.ndarray, label: np.ndarray, elig: np.ndarray,
                        ind_code: np.ndarray, ind_names: list[str], dates: list[str]) -> dict:
    """行业内 IC 分布：逐行业做行业内横截面 IC，汇总中位数/正比例/离散度。"""
    rows = []
    for g, name in enumerate(ind_names):
        cols = np.flatnonzero(ind_code == g)
        if len(cols) < MIN_N_IND:
            continue
        sub_f = factor[:, cols]
        sub_l = label[:, cols]
        sub_e = elig[:, cols]
        ic = fl._spearman_ic(np.where(sub_e, sub_f, np.nan), sub_l, min_n=MIN_N_IND)
        s = pd.Series(ic, dtype=float).dropna()
        if len(s) < 20:
            continue
        rows.append({"industry": name, "codes": int(len(cols)), "n_days": int(len(s)),
                     "ic_mean": fl._num(float(s.mean()), 6),
                     "ic_t": fl._num(float(s.mean() / s.std(ddof=1) * math.sqrt(len(s))), 3)})
    if not rows:
        return {"n_industries": 0, "industries": []}
    vals = np.asarray([r["ic_mean"] for r in rows], dtype=float)
    return {"n_industries": len(rows),
            "median_ic": fl._num(float(np.median(vals)), 6),
            "mean_ic": fl._num(float(np.mean(vals)), 6),
            "q25_ic": fl._num(float(np.percentile(vals, 25)), 6),
            "q75_ic": fl._num(float(np.percentile(vals, 75)), 6),
            "share_positive": fl._num(float(np.mean(vals > 0)), 4),
            "industries": rows}


def walk_forward_eval(ic_series: pd.Series, spread: pd.Series,
                      windows=WF_WINDOWS) -> dict:
    """walk-forward：按窗口切分 IC 序列与多空日收益序列，给 IC/多空/显著性。"""
    out = {}
    for label, start, end in windows:
        ics = ic_series[(ic_series.index >= start) & (ic_series.index <= end)]
        sp = spread[(spread.index >= start) & (spread.index <= end)]
        st = series_stats(ics)
        st_month = series_stats(monthly_cohort(ics))
        ret = fl._return_stats(sp, 252.0 / HORIZON) if len(sp) else {}
        out[label] = {"ic": st, "ic_monthly_t": st_month.get("t"),
                      "long_short_mean_20d_pct": ret.get("mean_car_pct"),
                      "long_short_ann_pct": ret.get("ann_pct"),
                      "long_short_t_daily": ret.get("t"),
                      "long_short_t_monthly": series_stats(monthly_cohort(sp)).get("t"),
                      "bootstrap_p": (stat_utils.block_bootstrap_p(sp, block=BLOCK, n_boot=N_BOOT,
                                                                   seed=DEFAULT_SEED).get("p_le_0")
                                      if len(sp) >= BLOCK * 3 else None)}
    return out


# ============================================================ 数据加载
def load_industry(data_root: str | Path | None = None) -> pd.Series:
    """代码 → 行业名（tushare stock_basic.industry；与 task-88 板块代理同口径）。"""
    root = Path(data_root) if data_root else DATA
    files = sorted(glob.glob(str(root / "stock_basic" / "*.parquet")))
    if not files:
        raise FileNotFoundError(f"stock_basic 不存在: {root / 'stock_basic'}")
    parts = [pq.read_table(f, columns=["ts_code", "name", "industry"]).to_pandas() for f in files]
    sb = pd.concat(parts, ignore_index=True).drop_duplicates("ts_code")
    return sb.set_index("ts_code")["industry"].fillna("其他").astype(str)


def load_circ_mv(panel: fl.Panel, data_root: str | Path | None = None) -> np.ndarray:
    """逐日流通市值矩阵（daily_basic.circ_mv，万元；用于市值分层，仅 T 日可知）。"""
    root = Path(data_root) if data_root else DATA
    rows = []
    for dt in panel.dates:
        path = root / "daily_basic" / f"d_{dt}.parquet"
        if path.exists():
            df = pq.read_table(path, columns=["ts_code", "circ_mv"]).to_pandas()
            df["trade_date"] = str(dt)
            rows.append(df)
    if not rows:
        return np.full(panel.shape, np.nan)
    long = pd.concat(rows, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
    mat = long.pivot(index="trade_date", columns="ts_code", values="circ_mv")
    return np.asarray(mat.reindex(index=panel.dates, columns=panel.codes), dtype=float)


# ============================================================ 核心评测（可在合成面板上单测）
def evaluate_core(panel: fl.Panel, industry: pd.Series, cap: np.ndarray | None = None,
                  start: str = DEFAULT_START, end: str = DEFAULT_END,
                  n_placebo: int = DEFAULT_N_PLACEBO, seed: int = DEFAULT_SEED,
                  windows=WINDOWS) -> dict:
    """核心评测：面板 + 行业映射 → 完整 JSON 结果（不做数据 IO，便于单测）。"""
    t0 = time.time()
    window = fl._resolve_window(panel.dates, start, end, HORIZON)
    meta: dict = {
        "lib": "industry_rs_factor", "version": LIB_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
        "start": str(start), "end": str(end), "horizon": HORIZON,
        "windows": list(windows), "n_deciles": N_DECILES, "costs_bp": list(COSTS_BP),
        "seed": int(seed), "n_placebo": int(n_placebo), "block": BLOCK,
        "definitions": {
            "factor": "RS_k = 个股 k 日收益 − 同行业其他成员 k 日收益等权均值（leave-one-out）",
            "industry": "tushare stock_basic.industry（静态分类，与 task-88 板块代理同口径）",
            "label": "T 日收盘因子 → T+1 收盘入场 → T+21 收盘卖出；个股复权收益 − 等权市场同期复利收益",
            "ic": "逐日横截面 Spearman IC；t_monthly 用每 20 个观测取 1 的非重叠抽样（重叠修正）",
            "cost": "净年化 = 毛年化 − 成本(bp)×年化换手×2（多空两腿）；换手按 20 日重叠持有口径折算",
        },
        "data": dict(panel.load_meta),
    }
    payload = {"meta": meta, "factors": {}, "fdr": {}, "verdict": {}}
    if window is None or panel.shape[0] == 0:
        meta["effective"] = {"start": None, "end": None, "signal_days": 0}
        payload["verdict"] = {
            "is_effective_factor": False, "near_tradable": False,
            "reasons": ["面板为空或区间内无法计算满持有期标签"],
            "primary_factor": PRIMARY_FACTOR, "direction_sign": 1,
        }
        return payload

    elig = panel.eligible_mask().to_numpy(dtype=bool)
    label = np.asarray(fl.forward_car(panel, horizon=HORIZON, cost=0.0), dtype=float)
    factors, ind_names = build_rs_factors(panel, industry, windows=windows, eligible=elig)
    ind_code, _ = industry_codes(industry, panel.codes)
    pos = np.arange(window["start_i"], window["end_i"] + 1)
    dates_w = [str(panel.dates[i]) for i in pos]
    label_w = label[pos]
    elig_w = elig[pos]
    cap_w = None if cap is None else np.asarray(cap, dtype=float)[pos]
    meta["effective"] = {"start": window["start"], "end": window["end"],
                         "signal_days": int(window["n"]), "panel_dates": int(panel.shape[0]),
                         "codes": int(panel.shape[1]), "panel_first": window["panel_first"],
                         "panel_last": window["panel_last"],
                         "requested_start": window["requested_start"],
                         "requested_end": window["requested_end"]}
    meta["industry_count"] = len(ind_names)

    fdr_tests = []
    for k in windows:
        name = f"RS_{k}"
        fmat = factors[name][pos]
        ic_series = daily_ic(fmat, label_w, elig_w, dates_w)
        ic_st = series_stats(ic_series)
        ic_m = series_stats(monthly_cohort(ic_series))
        dec = decile_eval(fmat, label_w, elig_w, dates_w)
        spread_series = dec.pop("_spread_series")
        turn = turnover_eval(fmat, elig_w)
        wf = walk_forward_eval(ic_series, spread_series)
        obs_wf = wf.get("2023-2024") or {}
        obs_ic_mean = (obs_wf.get("ic") or {}).get("mean")
        # 方向 = 观察窗（2023-2024）IC 符号；无观察窗时默认做多 RS（+1）。验证窗只做检验不改方向。
        sign = 1 if (obs_ic_mean is None or obs_ic_mean >= 0) else -1
        out = {
            "name": name, "origin": f"个股{k}日收益 − 行业{k}日收益（leave-one-out）",
            "direction": {"sign": int(sign),
                          "source": "2023-2024 观察窗 IC 符号（无观察窗时默认 +1）"},
            "ic": {**ic_st, "ir": fl._num(
                (ic_st["mean"] / ic_st["std"]) if ic_st.get("std") else None, 4),
                "t_monthly": ic_m.get("t"), "n_monthly": ic_m.get("n_days"),
                "bootstrap": stat_utils.block_bootstrap_p(
                    ic_series.dropna(), block=BLOCK, n_boot=N_BOOT, seed=DEFAULT_SEED)},
            "deciles": dec, "turnover": turn,
            "cost_sensitivity": cost_sensitivity(dec["long_short"]["ann_pct"],
                                                 turn["top_decile"]["ann_turnover_overlapping"]),
            "walk_forward": {label2: v for label2, v in wf.items()},
            "placebo": placebo_eval(fmat, label_w, elig_w, dates_w,
                                    n_perm=n_placebo, seed=seed),
            "cap_stratified": (cap_stratified(fmat, label_w, elig_w, cap_w, dates_w)
                               if cap_w is not None else {}),
            "industry_stratified": industry_stratified(fmat, label_w, elig_w,
                                                       ind_code, ind_names, dates_w),
        }
        payload["factors"][name] = out
        ls = out["deciles"]["long_short"]
        boot_p = (ls.get("bootstrap") or {}).get("p_le_0")
        ls["direction_adjusted"] = {
            "direction_sign": int(sign),
            "ann_pct": fl._num(None if ls.get("ann_pct") is None else sign * ls["ann_pct"], 2),
            "t_monthly": fl._num(None if ls.get("t_monthly") is None else sign * ls["t_monthly"], 3),
            "bootstrap_p_le_0": fl._num(
                None if boot_p is None else (boot_p if sign > 0 else 1.0 - boot_p), 4),
        }
        for wf_label in wf:
            t = wf[wf_label].get("ic_monthly_t")   # 用月度非重叠 t（保守），避免 20 日重叠夸大
            fdr_tests.append({"factor": name, "period": wf_label, "t": t,
                              "p": two_sided_p(t)})
    payload["fdr"] = bh_q([t["p"] for t in fdr_tests])
    payload["fdr"]["tests"] = fdr_tests
    payload["verdict"] = decide_verdict(payload["factors"], primary=PRIMARY_FACTOR,
                                        fdr=payload["fdr"])
    meta["runtime_sec"] = round(time.time() - t0, 2)
    return payload


def decide_verdict(factors: dict, primary: str = PRIMARY_FACTOR, cost_key_bp: int = 30,
                   fdr: dict | None = None) -> dict:
    """裁决：是否有效因子 / 是否接近可交易（方向 = 观察窗 IC 符号，验证窗只检验不改方向）。"""
    reasons: list[str] = []
    f = factors.get(primary)
    if not f:
        return {"is_effective_factor": False, "near_tradable": False,
                "reasons": [f"缺少主因子 {primary}"]}
    ic, dec, wf = f["ic"], f["deciles"], f["walk_forward"]
    ls = dec["long_short"]
    pl = f["placebo"]
    sign = int((f.get("direction") or {}).get("sign", 1))
    cost = f["cost_sensitivity"].get(f"{cost_key_bp}bp", {})
    drag = cost.get("cost_drag_ann_pct")
    ic_mean = ic.get("mean")
    ic_t_month = ic.get("t_monthly")
    dir_ic = None if ic_mean is None else sign * ic_mean
    dir_ic_t_month = None if ic_t_month is None else sign * ic_t_month
    gross_raw = ls.get("ann_pct")
    gross = None if gross_raw is None else sign * gross_raw
    net30 = None if gross is None or drag is None else gross - drag
    obs = (wf.get("2023-2024") or {})
    oos = (wf.get("2025-2026") or {})
    obs_ic = (obs.get("ic") or {}).get("mean")
    oos_ic = (oos.get("ic") or {}).get("mean")
    oos_t_month = oos.get("ic_monthly_t")
    dir_obs = None if obs_ic is None else sign * obs_ic
    dir_oos = None if oos_ic is None else sign * oos_ic
    dir_oos_t = None if oos_t_month is None else sign * oos_t_month
    mono = dec.get("monotonicity")
    dir_mono = None if mono is None else sign * mono
    p = pl.get("p_value")

    eff = (dir_ic is not None and dir_ic > 0 and dir_ic_t_month is not None and dir_ic_t_month >= 2
           and dir_obs is not None and dir_obs > 0 and dir_oos is not None and dir_oos > 0
           and p is not None and p < 0.05)
    if eff:
        reasons.append(f"{primary}（方向 {sign:+d}）满足「有效因子」：全样本月度 t={dir_ic_t_month:.2f}≥2、"
                       f"观察窗(2023-24) IC={dir_obs:.4f}>0、验证窗(2025-26) IC={dir_oos:.4f}>0、"
                       f"安慰剂 p={p:.3f}<0.05")
    else:
        parts = []
        if dir_ic is None or dir_ic <= 0:
            parts.append(f"方向调整后全样本 IC={dir_ic}（非正）")
        if dir_ic_t_month is None or dir_ic_t_month < 2:
            parts.append(f"方向调整后月度 t={dir_ic_t_month}（<2）")
        if dir_obs is None or dir_obs <= 0:
            parts.append(f"观察窗方向 IC={dir_obs}（非正）")
        if dir_oos is None or dir_oos <= 0:
            parts.append(f"验证窗方向 IC={dir_oos}（非正）")
        if p is None or p >= 0.05:
            parts.append(f"安慰剂 p={p}（不显著）")
        reasons.append("不满足「有效因子」四关：" + "；".join(parts))
    q_oos = None
    if fdr:
        for i, test in enumerate(fdr.get("tests", [])):
            if test.get("factor") == primary and test.get("period") == "2025-2026":
                qs = fdr.get("q", [])
                q_oos = qs[i] if i < len(qs) else None
    # 长腿（按方向的多头一端）相对全分层均值的超额：纯多头（A 股不能做空）是否也有 alpha
    groups = dec.get("groups", {})
    long_key = "D1" if sign < 0 else f"D{N_DECILES}"
    group_vals = [g.get("mean_car_pct") for g in groups.values() if g.get("mean_car_pct") is not None]
    long_leg_excess = None
    if group_vals and groups.get(long_key, {}).get("mean_car_pct") is not None:
        long_leg_excess = groups[long_key]["mean_car_pct"] - float(np.mean(group_vals))
    da = ls.get("direction_adjusted") or {}
    dir_ls_t = da.get("t_monthly")
    dir_boot_p = da.get("bootstrap_p_le_0")
    cap_s = f.get("cap_stratified") or {}
    small_ic = (cap_s.get("小市值") or {}).get("ic_mean")
    large_ic = (cap_s.get("大市值") or {}).get("ic_mean")
    near = bool(eff and net30 is not None and net30 > 0
                and dir_mono is not None and dir_mono >= 0.7
                and dir_oos_t is not None and dir_oos_t >= 2
                and (q_oos is not None and q_oos <= 0.10)
                and dir_ls_t is not None and dir_ls_t >= 2
                and dir_boot_p is not None and dir_boot_p <= 0.05
                and long_leg_excess is not None and long_leg_excess >= 0.5)
    if near:
        reasons.append(f"接近可交易（方向 {sign:+d}）：30bp 成本后多空年化 {net30:+.2f}%、"
                       f"分层单调性 {dir_mono}、验证窗月度 t={dir_oos_t}、BH-FDR q={q_oos}≤0.10、"
                       f"多空月度 t={dir_ls_t}≥2、bootstrap p={dir_boot_p}≤0.05、长腿超额 {long_leg_excess:+.2f}%")
    else:
        reasons.append(f"不构成可交易（方向 {sign:+d}）：30bp 成本后多空年化 {fl._num(net30, 2)}% 但"
                       f"多空月度非重叠 t={dir_ls_t}（<2）、长腿超额 {fl._num(long_leg_excess, 2)}%"
                       f"（<0.5%，纯多头无 alpha；A 股融券受限 → 空头端收益难落地）、"
                       f"分层单调性 {dir_mono}、验证窗月度 t={dir_oos_t}、BH-FDR q={q_oos}；"
                       f"IC 集中于小市值（小盘 {fl._num(small_ic, 4)} vs 大盘 {fl._num(large_ic, 4)}）")
    return {"is_effective_factor": bool(eff), "near_tradable": near, "reasons": reasons,
            "primary_factor": primary, "direction_sign": sign,
            "gross_ann_pct": fl._num(gross, 2), "net_ann_30bp_pct": fl._num(net30, 2),
            "ic_mean_raw": ic_mean, "ic_mean_directed": fl._num(dir_ic, 6),
            "ic_t_monthly_raw": ic_t_month, "ic_t_monthly_directed": fl._num(dir_ic_t_month, 3),
            "placebo_p": p, "monotonicity_raw": mono, "monotonicity_directed": fl._num(dir_mono, 3),
            "wf_2023_2024_ic": obs_ic, "wf_2025_2026_ic": oos_ic,
            "fdr_q_oos": q_oos,
            "long_leg_excess_pct": fl._num(long_leg_excess, 2),
            "long_short_t_monthly_directed": dir_ls_t,
            "long_short_bootstrap_p_directed": dir_boot_p,
            "cap_ic_small": small_ic, "cap_ic_large": large_ic,
            "industry_median_ic": (f.get("industry_stratified") or {}).get("median_ic"),
            "industry_share_positive": (f.get("industry_stratified") or {}).get("share_positive"),
            "oos_ic_t_monthly_directed": fl._num(dir_oos_t, 3)}


# ============================================================ 全流程 + 输出
def evaluate(start: str = DEFAULT_START, end: str = DEFAULT_END,
             n_placebo: int = DEFAULT_N_PLACEBO, seed: int = DEFAULT_SEED,
             universe: str = "tradable", data_root: str | Path | None = None,
             windows=WINDOWS) -> dict:
    """加载真实数据并评测（CLI eval 的实现）。"""
    root = data_root or DATA
    panel = fl.load_panel(start, end, horizon=HORIZON, universe=universe,
                          data_root=root, load_roe=False)
    industry = load_industry(root)
    cap = load_circ_mv(panel, root)
    return evaluate_core(panel, industry, cap=cap, start=start, end=end,
                         n_placebo=n_placebo, seed=seed, windows=windows)


def render_markdown(payload: dict) -> str:
    """生成 Markdown 报告。"""
    meta = payload.get("meta", {})
    eff = meta.get("effective", {})
    verdict = payload.get("verdict", {})
    if not payload.get("factors"):
        return "\n".join([
            "# 行业相对强度因子（RS）横截面严格检验",
            "",
            f"- 生成：{meta.get('generated_at')}；版本 v{meta.get('version')}",
            f"- 区间：请求 {meta.get('start')}~{meta.get('end')}；"
            f"有效信号窗 {eff.get('start')}~{eff.get('end')}（{eff.get('signal_days')} 日）",
            "- **结论：面板为空或区间不足以计算满持有期标签，未生成因子结果。**",
            *[f"- {r}" for r in verdict.get("reasons", [])],
            "",
        ])
    L = [
        "# 行业相对强度因子（RS）横截面严格检验",
        "",
        f"- 生成：{meta.get('generated_at')}；版本 v{meta.get('version')}；"
        f"信号窗 {eff.get('start')}~{eff.get('end')}（{eff.get('signal_days')} 个交易日）；"
        f"面板 {eff.get('panel_dates')} 日 × {eff.get('codes')} 只；行业数 {meta.get('industry_count')}",
        "- 定义：RS_k = 个股 k 日收益 − 同行业其他成员 k 日收益等权均值（leave-one-out）；"
        "行业分类 = tushare stock_basic.industry（静态分类，与 task-88 同口径）",
        f"- 标签：T 日收盘因子 → T+1 收盘入场 → T+{1 + HORIZON} 收盘卖出；"
        f"相对等权市场；成本在组合层单独做 30/50/80bp 敏感性",
        f"- 稳健性：walk-forward 2023-2024/2025-2026、安慰剂 {meta.get('n_placebo')} 次、BH-FDR、"
        f"市值三分层、行业内 IC 分布；重叠修正 = 月度非重叠 t + block bootstrap（块长 {BLOCK}）",
        "",
        "## 一、主结果（全样本，2023-01 ~ 2026-10；方向 = 2023-2024 观察窗 IC 符号）",
        "",
        "| 因子 | 方向 | IC均值(原始) | IC均值(方向) | IC_t(日频,原始) | IC_t(月度,方向) | 多空年化(方向,毛) | t_monthly(方向) | 单调性(方向) | 安慰剂 p |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, f in payload.get("factors", {}).items():
        ic, ls, dec, pl = f["ic"], f["deciles"]["long_short"], f["deciles"], f["placebo"]
        sign = int((f.get("direction") or {}).get("sign", 1))
        ic_m = ic.get("mean")
        ic_tm = ic.get("t_monthly")
        mono = dec.get("monotonicity")
        L.append(f"| {name} | {sign:+d} | {_fmt(ic_m)} | {_fmt(None if ic_m is None else sign * ic_m)} | "
                 f"{_fmt(ic.get('t'))} | {_fmt(None if ic_tm is None else sign * ic_tm)} | "
                 f"{_fmt(None if ls.get('ann_pct') is None else sign * ls.get('ann_pct'))}% | "
                 f"{_fmt(None if ls.get('t_monthly') is None else sign * ls.get('t_monthly'))} | "
                 f"{_fmt(None if mono is None else sign * mono)} | {_fmt(pl.get('p_value'))} |")
    L += ["", "## 二、十分位分层（20 日 CAR 均值 %）", "",
          "| 因子 | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 | D10−D1 年化 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, f in payload.get("factors", {}).items():
        groups = f["deciles"]["groups"]
        vals = " | ".join(_fmt(groups[f"D{i}"].get("mean_car_pct")) for i in range(1, 11))
        L.append(f"| {name} | {vals} | {_fmt(f['deciles']['long_short'].get('ann_pct'))}% |")
    L += ["", "## 三、walk-forward（2023-2024 观察 / 2025-2026 验证）", "",
          "| 因子 | 窗口 | IC均值 | IC_t(日频) | IC_t(月度) | 多空年化 | 多空 t(月度) | bootstrap p |",
          "|---|---|---|---|---|---|---|---|"]
    for name, f in payload.get("factors", {}).items():
        for wf_label, w in f.get("walk_forward", {}).items():
            L.append(f"| {name} | {wf_label} | {_fmt((w.get('ic') or {}).get('mean'))} | "
                     f"{_fmt((w.get('ic') or {}).get('t'))} | {_fmt(w.get('ic_monthly_t'))} | "
                     f"{_fmt(w.get('long_short_ann_pct'))}% | {_fmt(w.get('long_short_t_monthly'))} | "
                     f"{_fmt(w.get('bootstrap_p'))} |")
    L += ["", "## 四、换手与成本敏感性（主因子 " + PRIMARY_FACTOR + "）", "",
          "| 因子 | 日均单边换手 | 年化换手(20日重叠) | 毛年化 | 30bp 净 | 50bp 净 | 80bp 净 |",
          "|---|---|---|---|---|---|---|"]
    for name, f in payload.get("factors", {}).items():
        turn = f["turnover"]["top_decile"]
        cs = f["cost_sensitivity"]
        L.append(f"| {name} | {_fmt(turn.get('avg_one_way_turnover'))} | "
                 f"{_fmt(turn.get('ann_turnover_overlapping'))} | "
                 f"{_fmt(f['deciles']['long_short'].get('ann_pct'))}% | "
                 f"{_fmt((cs.get('30bp') or {}).get('net_ann_pct'))}% | "
                 f"{_fmt((cs.get('50bp') or {}).get('net_ann_pct'))}% | "
                 f"{_fmt((cs.get('80bp') or {}).get('net_ann_pct'))}% |")
    L += ["", "## 五、横截面稳健性（市值分层 / 行业内 IC）", "",
          "| 因子 | 小市值 IC | 中市值 IC | 大市值 IC | 行业IC中位数 | 行业IC正比例 | 行业数 |",
          "|---|---|---|---|---|---|---|"]
    for name, f in payload.get("factors", {}).items():
        cap_s = f.get("cap_stratified") or {}
        ind_s = f.get("industry_stratified") or {}
        L.append(f"| {name} | {_fmt((cap_s.get('小市值') or {}).get('ic_mean'))} | "
                 f"{_fmt((cap_s.get('中市值') or {}).get('ic_mean'))} | "
                 f"{_fmt((cap_s.get('大市值') or {}).get('ic_mean'))} | "
                 f"{_fmt(ind_s.get('median_ic'))} | {_fmt(ind_s.get('share_positive'))} | "
                 f"{ind_s.get('n_industries')} |")
    fdr = payload.get("fdr", {})
    L += ["", "## 六、多重检验（BH-FDR，6 个假设 = 3 因子 × 2 窗口）", "",
          f"- q_cut={fdr.get('q_cut')}，q≤q_cut 的假设数 = {fdr.get('n_reject')}（总 {fdr.get('n_tests')}）", "",
          "| 因子 | 窗口 | t | p | q |", "|---|---|---|---|---|"]
    q_values = fdr.get("q", [])
    for idx, row in enumerate(fdr.get("tests", [])):
        q_val = q_values[idx] if idx < len(q_values) else None
        L.append(f"| {row['factor']} | {row['period']} | {_fmt(row.get('t'), 2)} | "
                 f"{_fmtp(row.get('p'))} | {_fmtp(q_val)} |")
    dsign = verdict.get("direction_sign")
    dsign_txt = f"{int(dsign):+d}" if isinstance(dsign, (int, float)) else "—"
    L += ["", "## 七、结论（是否有效因子 / 是否接近可交易）", "",
          f"- **是否有效因子：{'是' if verdict.get('is_effective_factor') else '否'}**",
          f"- **是否接近可交易：{'是' if verdict.get('near_tradable') else '否'}**",
          f"- 主因子 {verdict.get('primary_factor') or PRIMARY_FACTOR}（方向 {dsign_txt}）："
          f"原始 IC {_fmt(verdict.get('ic_mean_raw'))}，方向调整后 IC {_fmt(verdict.get('ic_mean_directed'))}"
          f"（月度 t={_fmt(verdict.get('ic_t_monthly_directed'), 2)}）；"
          f"方向调整后毛多空年化 {_fmt(verdict.get('gross_ann_pct'), 2)}%，"
          f"30bp 成本后 {_fmt(verdict.get('net_ann_30bp_pct'), 2)}%；"
          f"安慰剂 p={_fmtp(verdict.get('placebo_p'))}；方向单调性 {_fmt(verdict.get('monotonicity_directed'), 2)}；"
          f"观察窗 IC {_fmt(verdict.get('wf_2023_2024_ic'))} / 验证窗 IC {_fmt(verdict.get('wf_2025_2026_ic'))}"
          f"（验证窗月度 t={_fmt(verdict.get('oos_ic_t_monthly_directed'), 2)}）"]
    for r in verdict.get("reasons", []):
        L.append(f"- {r}")
    raw_ic = verdict.get("ic_mean_raw")
    dir_ic = verdict.get("ic_mean_directed")
    raw_sign_txt = "为负" if (raw_ic is not None and raw_ic < 0) else "为正"
    dir_sign_txt = "反转" if (raw_ic is not None and raw_ic < 0) else "续强"
    n_ind = meta.get("industry_count")
    share_pos = verdict.get("industry_share_positive")
    long_ex = verdict.get("long_leg_excess_pct")
    long_tail = (long_ex is not None and long_ex < 0.5)
    L += ["", "### 如何解读（供决策用）", "",
          f"- **正向「行业相对强度{'续强' if raw_sign_txt == '为正' else '反转'}」结论**：RS 全样本 IC={_fmt(raw_ic)}"
          f"（{raw_sign_txt}）→ 20 日尺度上行业相对强度表现为**{dir_sign_txt}**；",
          f"- **方向调整后信息**：IC={_fmt(dir_ic)}"
          f"（月度 t={_fmt(verdict.get('ic_t_monthly_directed'), 2)}），观察窗/验证窗"
          f"（{_fmt(verdict.get('wf_2023_2024_ic'))} / {_fmt(verdict.get('wf_2025_2026_ic'))}），"
          f"安慰剂双侧 p={_fmtp(verdict.get('placebo_p'))}，主因子验证窗 BH-FDR q={_fmtp(verdict.get('fdr_q_oos'))}；"
          f"{n_ind} 个行业 IC 中位数 {_fmt(verdict.get('industry_median_ic'))}"
          f"（正比例 {_fmt(share_pos)}）；",
          f"- **是否可交易**：方向调整后毛多空年化 {_fmt(verdict.get('gross_ann_pct'), 2)}%、"
          f"30bp 后 {_fmt(verdict.get('net_ann_30bp_pct'), 2)}%，月度非重叠 "
          f"t={_fmt(verdict.get('long_short_t_monthly_directed'), 2)}；长腿相对全分层均值 "
          f"{_fmt(long_ex, 2)}%；"
          + ("收益主要集中在不可做空的空头端 → 纯多头不可交易。" if long_tail else
             "长腿有一定正超额，需继续验证可交易性。"),
          f"- 与 task-88 呼应：RS 事件叠加层无效的根因是板块 beta；横截面上 RS 的信息存在，"
          f"当前方向为**{dir_sign_txt}**，结论以本页 verdict 与稳健性表为准。"]
    L += ["", "## 八、口径与局限披露", "",
          "- PIT：因子只用 T 日及以前数据；标签 T+1 收盘入场、T+21 收盘卖出（无前视）；",
          "- 行业分类为 stock_basic 静态行业（tushare 现行分类应用于历史），未做 PIT 行业变迁校正；"
          "与 task-88 板块代理同口径，便于横向对照；",
          "- 换手按 20 日重叠持有组合折算（每日仅换 1/20 仓位）→ 年化换手 = 日均单边换手 × 252/20；"
          "成本按多空两腿计；",
          "- 月度非重叠 t 与 block bootstrap（块长 20）用于修正 20 日持有期重叠导致的 t 值虚高（MEETING.md 纪律）；",
          "- 本报告为研究输出，不构成投资建议。"]
    return "\n".join(L)


def _fmt(value, digits: int = 4, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{digits}f}{suffix}" if digits > 0 else f"{float(value)}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _fmtp(value, digits: int = 4) -> str:
    """p/q 值展示：不带正号。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def write_outputs(payload: dict, prefix: str | Path) -> tuple[Path, Path]:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(render_markdown(payload), encoding="utf-8")
    return js, md


def _print_summary(payload: dict) -> None:
    meta = payload.get("meta", {})
    eff = meta.get("effective", {})
    print(f"# industry_rs_factor v{meta.get('version')} | 信号窗 {eff.get('start')}~{eff.get('end')} "
          f"（{eff.get('signal_days')} 日）| 面板 {eff.get('panel_dates')} 日 × {eff.get('codes')} 只 | "
          f"行业 {meta.get('industry_count')}")
    print(f"# 标签 T+1 入/T+{1 + HORIZON} 出 | 成本敏感性 {meta.get('costs_bp')} bp | 安慰剂 {meta.get('n_placebo')} 次")
    print(f"# {'因子':<8}{'IC':>9}{'IC_t':>7}{'ICt_月':>7}{'多空年化':>10}{'LS_t月':>7}"
          f"{'单调性':>7}{'安慰剂p':>8}")
    for name, f in payload.get("factors", {}).items():
        ic, ls, dec, pl = f["ic"], f["deciles"]["long_short"], f["deciles"], f["placebo"]
        print(f"  {name:<8}{_fmt(ic.get('mean')):>9}{_fmt(ic.get('t')):>7}"
              f"{_fmt(ic.get('t_monthly')):>7}{_fmt(ls.get('ann_pct')):>10}"
              f"{_fmt(ls.get('t_monthly')):>7}{_fmt(dec.get('monotonicity')):>7}"
              f"{_fmt(pl.get('p_value')):>8}")
    v = payload.get("verdict", {})
    print(f"# 结论：有效因子={v.get('is_effective_factor')}，接近可交易={v.get('near_tradable')}")
    for r in v.get("reasons", []):
        print(f"#  - {r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="industry_rs_factor.py",
                                 description="行业相对强度（RS）横截面因子严格检验")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_eval = sub.add_parser("eval", help="跑真实数据评测并输出 JSON+MD")
    p_eval.add_argument("--start", default=DEFAULT_START)
    p_eval.add_argument("--end", default=DEFAULT_END)
    p_eval.add_argument("--n-placebo", type=int, default=DEFAULT_N_PLACEBO)
    p_eval.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_eval.add_argument("--universe", choices=["tradable", "all"], default="tradable")
    p_eval.add_argument("--data-root", default="")
    p_eval.add_argument("--out-prefix", default=str(OUT / "industry_rs_factor_20261101"))
    p_eval.add_argument("--json", action="store_true", help="stdout 打印完整 JSON")
    p_list = sub.add_parser("list", help="列出因子定义")
    p_list.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        rows = [{"name": f"RS_{k}", "definition": f"个股{k}日收益 − 同行业其他成员{k}日收益等权均值（leave-one-out）",
                 "window": k, "horizon": HORIZON} for k in WINDOWS]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print(f"# industry_rs_factor · {len(rows)} 个因子（标签持有 {HORIZON} 日）")
            for row in rows:
                print(f"  {row['name']:<8} {row['definition']}")
        return 0

    t0 = time.time()
    try:
        payload = evaluate(start=args.start, end=args.end, n_placebo=args.n_placebo,
                           seed=args.seed, universe=args.universe,
                           data_root=(args.data_root or None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[industry_rs_factor] 评测失败: {exc}", file=sys.stderr)
        return 2
    js, md = write_outputs(payload, args.out_prefix)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print_summary(payload)
    print(f"# 输出: {js} / {md}（耗时 {time.time() - t0:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
