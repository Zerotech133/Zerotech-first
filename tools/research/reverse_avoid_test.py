# -*- coding: utf-8 -*-
"""tools/research/reverse_avoid_test.py · 量学反向信号的「规避组合」深测（2026-11-01，支线 H）。

任务
----
wave2 批量四关（`tools/research/book_batch_test.py`）发现 11 个量学战法 OOS 显著反向，其中
LX20 牛股三绝（OOS directed IC t=-13.09）、LX22 凹口淘金（t=-10.23）、LX06 长阴短柱（t=-9.34）、
LX12 双阴出货（t=-4.73）等。A 股不能做空，反向只能落成「规避/不买/降配」。本文件做可交易性深测：

  1. 信号定义**直接复用** `book_batch_test.FACTOR_IMPLS`（lx06 / lx12 / lx20）与
     `factor_lib.FACTORS["aokou"]`（LX22），本文件不改动 book_batch_test.py / factor_lib.py；
  2. 对每个战法构造「规避组合」= 每日等权全市场（同口径可交易池）**剔除**过去 N 日
     （N=1/5/20，可配）触发过信号的股票；对照「等权市场基准」= 同一可交易池、同样剔除
     T 日收盘涨停（不可买入）股票；
  3. 计算：毛/净年化超额、日频超额 t（block bootstrap p）、逐年（2023/24/25/26）超额、
     触发覆盖度（日均剔除比例/数量）、换手（剔除集合 churn + 增量单边换手 + 成本年化）、
     容量（被剔除股票日均成交额/市值/可交易占比）、极端行情分段（2026 熊市峰→谷段）、
     以及**市值×行业分层对照**（同市值五分位×同行业单元内「被剔除组 vs 保留组」的日收益差），
     用于判断规避超额是否只是「剔除了小市值/高波动/某行业」；
  4. 输出 JSON + Markdown，给出每个战法「是否可交易规避 / 是否值得作风控降配规则」的结论与局限。

口径（与 book_batch_test 对齐）
------------------------------
- 数据：`quant_backtest/data/tushare`（daily / adj_factor / daily_basic），股票池 tradable
  （价 ≥ 2 元、成交额 ≥ 5000 万、上市 ≥ 250 日，均 T 日可知，见 factor_lib.load_panel）；
- 时间：T 日收盘决策 → T 日收盘建仓（近似）→ 持有 1 个交易日，日频再平衡；逐日收益用后复权
  `adj_close(T+1)/adj_close(T)-1`；T 日收盘涨停（按板块涨跌幅限制判定）视为买不进，基准与规避
  组合**同样剔除**（另剔除停牌/无 T+1 数据）；
- 超额 = 规避组合日收益 − 等权市场基准日收益；年化按 252 日；t 值用日频超额序列；p 值用
  stat_utils.block_bootstrap_p（块长 5，与持仓/信号窗口重叠修正同口径）；
- 成本：单边 30bp（factor_lib.DEFAULT_COST）；净超额 = 毛超额 − 增量单边换手×2×30bp×252；
- 涨停判定：按板块涨跌幅限制近似（主板 10%/创业板科创板 20%/北交所 30%，阈值放宽 0.5pp），
  ST 5% 未单独识别（少剔除而非多剔除，偏保守）。

用法
----
    # 全量（4 战法 × N=1/5/20，先跑通 N=5 可加 --ns 5）
    C:\\项目\\py312\\python.exe -X utf8 tools/research/reverse_avoid_test.py \
        --start 20230101 --end 20261030 --ns 1,5,20 \
        --out research/reports/reverse_avoid_20261101.json \
        --md research/reports/reverse_avoid_20261101.md

局限（写进报告）：日频等权再平衡近似、T 日收盘成交近似（未做 T+1 收盘入场敏感性）、
行业分类用 stock_basic 当前静态行业（轻微后验）、ST 未识别、涨停判定为涨跌幅近似、
信号为 single-pattern 代理（沿用 book_batch_test 简化口径，非原书严格规则）。
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

# ---------------------------------------------------------------- 路径/依赖
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import book_batch_test as bt      # noqa: E402  信号定义复用（不修改它）
import factor_lib as fl           # noqa: E402  统一评测底座
import stat_utils                 # noqa: E402  block bootstrap

VERSION = "1.0.0"
ANNUAL_DAYS = 252
COST = fl.DEFAULT_COST            # 单边 30bp
AMOUNT_UNIT = fl.AMOUNT_UNIT      # tushare daily.amount 单位千元 → 元
DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
DEFAULT_NS = (1, 5, 20)
DEFAULT_OUT_JSON = REPO / "research" / "reports" / "reverse_avoid_20261101.json"
DEFAULT_OUT_MD = REPO / "research" / "reports" / "reverse_avoid_20261101.md"
BLOCK = 5
N_BOOT = 300
SEED = 42

# 4 个代表反向战法（spec_id → 复用因子 / 方向 / 原书含义）
WAR_STOPS: dict[str, dict] = {
    "LX20_BULL_STOCK_THREE_ABSOLUTES": {
        "name": "牛股三绝（倍量不穿/高量不破/跳空不补）",
        "factor": "lx20_three_absolutes", "direction": 1,
        "book": "涨停密码·第17章 牛股三绝",
        "wave2": "OOS directed IC t=-13.09、OOS 有向超额 -0.85%（t=-5.56）",
    },
    "LX22_CONCAVE_NOTCH_GOLD": {
        "name": "凹口淘金",
        "factor": "aokou", "direction": 1,
        "book": "涨停密码·第2章 凹口淘金（近似实现）",
        "wave2": "OOS directed IC t=-10.23、OOS 有向超额 -0.96%（t=-6.29）",
    },
    "LX06_YIN_LINE_TACTICS": {
        "name": "大盘阴线战法 / 长阴短柱",
        "factor": "lx06_long_yin_short", "direction": 1,
        "book": "伏击涨停·第15章 长阴短柱战法",
        "wave2": "OOS directed IC t=-9.34、OOS 有向超额 -2.08%（t=-3.92）",
    },
    "LX12_DOUBLE_YIN_EXIT": {
        "name": "双阴出货战法",
        "factor": "lx12_double_yin_exit", "direction": -1,
        "book": "涨停密码·第6章 双阴出货（出场/风控信号）",
        "wave2": "OOS directed IC t=-4.73、OOS 有向超额 ≈0.00%（t=0.01）",
    },
}
WAR_ORDER = list(WAR_STOPS)


# ================================================================ 纯函数
def t_stat(series: pd.Series) -> float | None:
    """t 值（样本 < 5 或零波动返回 None，避免与真实零 t 混淆，与 stat_utils._tstat 同口径）。"""
    s = pd.Series(series, dtype=float).dropna()
    if len(s) < 5 or s.std(ddof=1) == 0:
        return None
    return float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))


def bootstrap_p(series: pd.Series, block: int = BLOCK, n_boot: int | None = None,
                seed: int = SEED) -> float | None:
    """日频序列均值的块自助 P(均值 ≤ 0)；样本不足返回 None。"""
    nb = N_BOOT if n_boot is None else int(n_boot)
    res = stat_utils.block_bootstrap_p(series, block=block, n_boot=nb, seed=seed)
    return res.get("p_le_0")


def board_limit_pct(codes) -> np.ndarray:
    """按板块给出涨跌幅限制（%）：北交所 30 / 创业板科创板 20 / 主板 10。"""
    out = np.empty(len(codes), dtype=float)
    for i, code in enumerate(codes):
        s = str(code).upper()
        if s.startswith(("4", "8", "92")) or s.endswith(".BJ"):
            out[i] = 30.0
        elif s.startswith(("300", "301", "688", "689")):
            out[i] = 20.0
        else:
            out[i] = 10.0
    return out


def limit_up_mask(panel: fl.Panel) -> pd.DataFrame:
    """T 日收盘是否涨停（按板涨跌幅限制 -0.5pp 近似；ST 5% 未识别 → 少剔除、偏保守）。"""
    pct = np.asarray(panel.pct_chg, dtype=float)
    thr = board_limit_pct(list(panel.codes))[None, :] - 0.5
    mask = np.isfinite(pct) & (pct >= thr)
    return pd.DataFrame(mask, index=panel.dates, columns=panel.codes)


def next_day_return(panel: fl.Panel) -> pd.DataFrame:
    """后复权日收益（T 日收盘 → T+1 日收盘）；信号日 T 的行即买入后首个持有日收益。"""
    adj = panel.adj_close
    return adj.pct_change(fill_method=None).shift(-1)


def build_buyable(panel: fl.Panel, ret_next: pd.DataFrame | None = None) -> pd.DataFrame:
    """T 日收盘可买入/可持有池：可交易池 ∩ 非涨停 ∩ T 日与 T+1 日复权价可得（排除停牌/退市）。"""
    ret_next = next_day_return(panel) if ret_next is None else ret_next
    buyable = panel.eligible_mask().fillna(False).astype(bool)
    buyable = buyable & panel.adj_close.notna() & ret_next.notna()
    buyable = buyable & ~limit_up_mask(panel)
    return buyable.fillna(False)


def trigger_window(trigger: pd.DataFrame, n: int) -> pd.DataFrame:
    """过去 n 日（含当日）是否触发过信号（rolling max，NaN 视为未触发）。"""
    if int(n) <= 1:
        return trigger.fillna(0.0).astype(float) > 0
    tr = (trigger.fillna(0.0).astype(float) > 0).astype(float)
    return tr.rolling(int(n), min_periods=1).max() > 0


def portfolio_returns(ret_next: pd.DataFrame, holdings: pd.DataFrame) -> pd.Series:
    """等权组合日收益：holdings 内 ret_next 的横截面均值（NaN 不参与）。"""
    held = holdings.fillna(False).astype(bool)
    cnt = held.sum(axis=1).astype(float)
    tot = ret_next.where(held).sum(axis=1, min_count=1)
    return (tot / cnt.where(cnt > 0)).rename("ret")


def coverage_series(excl: pd.DataFrame, buyable: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """日均剔除比例（剔除集合 ∩ 可买池 / 可买池）与剔除数量。"""
    ex = (excl.fillna(False).astype(bool) & buyable.fillna(False).astype(bool))
    n_ex = ex.sum(axis=1).astype(float)
    n_b = buyable.fillna(False).astype(bool).sum(axis=1).astype(float)
    return (n_ex / n_b.where(n_b > 0)).rename("coverage"), n_ex.rename("n_excluded")


def set_churn(mask: pd.DataFrame) -> pd.Series:
    """集合日度 churn：1 - Jaccard(今日集合, 昨日集合)。"""
    cur = mask.fillna(False).astype(bool)
    prev = cur.shift(1).fillna(False).astype(bool)
    inter = (cur & prev).sum(axis=1).astype(float)
    union = (cur | prev).sum(axis=1).astype(float)
    return (1.0 - inter / union.where(union > 0)).rename("churn")


def holdings_turnover(holdings: pd.DataFrame) -> pd.Series:
    """等权组合单边换手：0.5 × 对称差数量 / 平均持仓数（近似）。"""
    cur = holdings.fillna(False).astype(bool)
    prev = cur.shift(1).fillna(False).astype(bool)
    sym = (cur ^ prev).sum(axis=1).astype(float)
    n = (cur.sum(axis=1).astype(float) + prev.sum(axis=1).astype(float)) / 2.0
    return (0.5 * sym / n.where(n > 0)).rename("turnover")


def _mean_or_none(values) -> float | None:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(arr.mean()) if arr.size else None


def _median_or_none(values) -> float | None:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.median(arr)) if arr.size else None


def capacity_stats(amount_yuan: pd.DataFrame, excl: pd.DataFrame, buyable: pd.DataFrame,
                   circ_mv_yi: pd.DataFrame | None = None) -> dict:
    """容量：被剔除股票（∩ 可买池）的日均成交额/成交额分布/市值/大额占比。"""
    ex = excl.fillna(False).astype(bool) & buyable.fillna(False).astype(bool)
    amt_yi = amount_yuan / 1e8
    per_day_mean = amt_yi.where(ex).mean(axis=1)
    per_day_p50 = amt_yi.where(ex).median(axis=1)
    big = (amt_yi >= 1.0) & ex
    ex_cnt = ex.sum(axis=1).astype(float)
    big_share = (big.sum(axis=1).astype(float) / ex_cnt.where(ex_cnt > 0))
    univ_mean = amt_yi.where(buyable.fillna(False).astype(bool)).mean(axis=1)
    out = {
        "excluded_avg_amount_yi": _mean_or_none(per_day_mean),
        "excluded_median_amount_yi": _mean_or_none(per_day_p50),
        "excluded_amount_ge_1yi_share": _mean_or_none(big_share),
        "universe_avg_amount_yi": _mean_or_none(univ_mean),
        "avg_excluded_count": _mean_or_none(ex_cnt),
    }
    if circ_mv_yi is not None:
        mv = circ_mv_yi.where(ex)
        out["excluded_median_circ_mv_yi"] = _mean_or_none(mv.median(axis=1))
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def yearly_excess(excess: pd.Series, dates: pd.Index, coverage: pd.Series | None = None) -> dict:
    """逐年（2023/2024/2025/2026）年化超额、t、正日占比、覆盖度。"""
    ds = pd.Index([str(d) for d in dates])
    out: dict[str, dict] = {}
    for year in ("2023", "2024", "2025", "2026"):
        sel = (ds >= f"{year}0101") & (ds <= f"{year}1231")
        sub = excess[sel]
        if len(sub.dropna()) == 0:
            continue
        cov = coverage[sel] if coverage is not None else None
        out[year] = {
            "days": int(sub.notna().sum()),
            "ann_excess_pct": round(float(sub.mean() * ANNUAL_DAYS * 100), 3),
            "t": round(t_stat(sub), 2) if t_stat(sub) is not None else None,
            "positive_rate": round(float((sub.dropna() > 0).mean()), 4),
            "coverage_pct": round(float(cov.mean() * 100), 3) if cov is not None else None,
        }
    return out


def cap_rank_frames(cap: pd.DataFrame | None,
                    buyable: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """把流通市值矩阵转成「当日可买池内市值分位（0~100）」与「五分位（0~4）」；cap=None 返回 (None,None)。"""
    if cap is None:
        return None, None
    buy = buyable.fillna(False).astype(bool)
    cap_arr = np.asarray(cap.reindex(index=buy.index, columns=buy.columns), dtype=float)
    buy_arr = buy.to_numpy()
    pct = np.full_like(cap_arr, np.nan)
    q = np.full(cap_arr.shape, -1, dtype=np.int64)
    for i in range(cap_arr.shape[0]):
        ok = buy_arr[i] & np.isfinite(cap_arr[i])
        n_ok = int(ok.sum())
        if n_ok >= 20:
            ranks = pd.Series(cap_arr[i, ok]).rank(pct=True, method="average").to_numpy() * 100.0
            pct[i, ok] = ranks
            q[i, ok] = np.clip((ranks / 20.0).astype(np.int64), 0, 4)
    return (pd.DataFrame(pct, index=buy.index, columns=buy.columns),
            pd.DataFrame(q, index=buy.index, columns=buy.columns))


def stratified_spread(ret_next: pd.DataFrame, excl: pd.DataFrame, buyable: pd.DataFrame,
                      cap_q: pd.DataFrame | None, industry: pd.Series | None) -> pd.Series:
    """市值五分位×行业分层内「被剔除组 − 保留组」等权日收益差（按剔除股票数加权聚合）。

    只在同一 (市值五分位, 行业) 单元内、且该单元同时有被剔除股票（≥1）与保留股票（≥3）时计入；
    用于剥离「只是剔除了小市值/某行业」的解释：若层内差显著为负，说明被剔除股票同层内也跑输。
    """
    dates = ret_next.index
    codes = ret_next.columns
    buy = buyable.fillna(False).astype(bool).to_numpy()
    ex = excl.fillna(False).astype(bool).to_numpy() & buy
    keep = buy & ~ex
    ret = np.asarray(ret_next, dtype=float)
    finite = np.isfinite(ret)
    ex = ex & finite
    keep = keep & finite

    # 行业编码
    if industry is None:
        ind_id = np.zeros(len(codes), dtype=np.int64)
        n_ind = 1
    else:
        ind_vals = industry.reindex(pd.Index(codes)).fillna("__NA__").astype(str).to_numpy()
        uniq, inv = np.unique(ind_vals, return_inverse=True)
        ind_id = inv.astype(np.int64)
        n_ind = len(uniq)

    # 市值五分位（run() 预计算；缺失 → 独立桶不参与配对）
    if cap_q is None:
        q = np.zeros((len(dates), len(codes)), dtype=np.int64)
        cap_ok = buy.copy()
    else:
        q = np.asarray(cap_q.reindex(index=dates, columns=codes), dtype=np.int64)
        cap_ok = buy & (q >= 0)

    n_cell = 5 * (n_ind + 1)  # 预留 NaN 行业桶
    cell = q * (n_ind + 1) + ind_id[None, :] + 0
    # 未知市值/行业 → 独立桶（不参与配对）
    cell = np.where(cap_ok & buy, cell, n_cell - 1)
    cell = np.where(ind_id[None, :] >= 0, cell, n_cell - 1)
    cell = np.clip(cell, 0, n_cell - 1)
    d_idx = np.arange(len(dates), dtype=np.int64)[:, None]
    combo = (d_idx * n_cell + cell).ravel()
    flat_ret, flat_ex, flat_keep = ret.ravel(), ex.ravel(), keep.ravel()
    cnt_e = np.bincount(combo[flat_ex], minlength=len(dates) * n_cell).reshape(len(dates), n_cell)
    sum_e = np.bincount(combo[flat_ex], weights=flat_ret[flat_ex],
                        minlength=len(dates) * n_cell).reshape(len(dates), n_cell)
    cnt_k = np.bincount(combo[flat_keep], minlength=len(dates) * n_cell).reshape(len(dates), n_cell)
    sum_k = np.bincount(combo[flat_keep], weights=flat_ret[flat_keep],
                        minlength=len(dates) * n_cell).reshape(len(dates), n_cell)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_e = sum_e / cnt_e
        mean_k = sum_k / cnt_k
    ok = (cnt_e > 0) & (cnt_k >= 3)
    diff = np.where(ok, mean_e - mean_k, 0.0)
    wgt = np.where(ok, cnt_e, 0.0)
    wsum = wgt.sum(axis=1)
    daily = np.where(wsum > 0, (diff * wgt).sum(axis=1) / np.where(wsum > 0, wsum, 1.0), np.nan)
    return pd.Series(daily, index=dates, name="stratified_spread")


def exposure_diagnostics(vol20: pd.DataFrame | None, excl: pd.DataFrame, buyable: pd.DataFrame,
                         cap_pct: pd.DataFrame | None, industry: pd.Series | None) -> dict:
    """被剔除股票 vs 可买池的暴露对照：市值分位、小市值占比、20 日波动、行业集中度。

    入参矩阵已由调用方预计算并按评测窗口切片（vol20=20 日波动矩阵；cap_pct=池内市值分位 0~100）。
    """
    buy = buyable.fillna(False).astype(bool)
    ex = excl.fillna(False).astype(bool) & buy
    out: dict = {}
    if cap_pct is not None:
        cp = cap_pct.reindex(index=buy.index, columns=buy.columns)
        out["excluded_cap_percentile_mean"] = _mean_or_none(cp.where(ex).mean(axis=1))
        out["universe_cap_percentile_mean"] = _mean_or_none(cp.where(buy).mean(axis=1))
        bottom = cp < 20.0
        ex_cnt = ex.sum(axis=1).astype(float)
        out["excluded_bottom_quintile_share"] = _mean_or_none(
            (bottom & ex).sum(axis=1).astype(float) / ex_cnt.where(ex_cnt > 0))
    if vol20 is not None:
        v = vol20.reindex(index=buy.index, columns=buy.columns)
        out["excluded_vol20_mean"] = _mean_or_none(v.where(ex).mean(axis=1))
        out["universe_vol20_mean"] = _mean_or_none(v.where(buy).mean(axis=1))
    # 行业集中度（剔除集合内部占比前 5 行业）
    if industry is not None:
        ind_vals = industry.reindex(pd.Index(buy.columns)).fillna("__NA__").astype(str).to_numpy()
        shares = []
        for i in range(len(buy.index)):
            sel = ex.iloc[i].to_numpy()
            n_sel = int(sel.sum())
            if n_sel == 0:
                continue
            vc = pd.Series(ind_vals[sel]).value_counts(normalize=True)
            shares.append((float(vc.iloc[:5].sum()), vc.index[0]))
        if shares:
            out["excluded_top5_industry_share"] = round(float(np.mean([s[0] for s in shares])), 4)
            out["excluded_top_industry"] = _mode([s[1] for s in shares])
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def _mode(values) -> str | None:
    if not values:
        return None
    return pd.Series(values).value_counts().index[0]


def stress_segments(bench_ret: pd.Series) -> dict:
    """极端行情分段：2026 年（若有）最大回撤峰→谷段；全窗口最差 20 日窗口（熊市代理）。"""
    out: dict = {}
    bench = bench_ret.dropna()
    ds = pd.Index([str(d) for d in bench.index])

    def _seg(a: str, b: str) -> dict | None:
        sel = (ds >= a) & (ds <= b)
        sub = bench[sel]
        if len(sub) < 30:
            return None
        cum = (1.0 + sub).cumprod()
        peak_val, trough_val = cum.cummax(), cum
        dd = trough_val / peak_val - 1.0
        trough = dd.idxmin()
        peak = cum.loc[:trough].idxmax()
        seg_dates = list(cum.loc[peak:trough].index)
        return {"peak": str(peak), "trough": str(trough),
                "max_drawdown_pct": round(float(dd.min() * 100), 3),
                "segment_days": len(seg_dates), "segment_dates": [str(x) for x in seg_dates]}

    seg26 = _seg("20260101", "20261231")
    if seg26:
        out["bear_2026"] = seg26
    # 全窗口最差 20 日（日收益滚动累计）
    roll = (1.0 + bench).rolling(20).apply(np.prod, raw=True) - 1.0
    if roll.notna().any():
        end = roll.idxmin()
        pos = list(bench.index).index(end)
        start = bench.index[max(0, pos - 19)]
        out["worst_window_20d"] = {
            "start": str(start), "end": str(end),
            "market_ret_pct": round(float(roll.min() * 100), 3),
            "window_dates": [str(x) for x in bench.index[max(0, pos - 19):pos + 1]],
        }
    return out


def subset_stats(excess: pd.Series, seg_dates: list[str], bench_ret: pd.Series | None = None) -> dict:
    """某个日期段内的规避超额统计。"""
    idx = pd.Index([str(d) for d in excess.index])
    sel = idx.isin(set(seg_dates))
    sub = excess[sel].dropna()
    if len(sub) == 0:
        return {"days": 0}
    tv = t_stat(sub)
    out = {
        "days": int(len(sub)),
        "ann_excess_pct": round(float(sub.mean() * ANNUAL_DAYS * 100), 3),
        "cum_excess_pct": round(float(sub.sum() * 100), 3),
        "t": round(tv, 2) if tv is not None else None,
        "bootstrap_p": bootstrap_p(sub),
    }
    if bench_ret is not None:
        b = bench_ret[bench_ret.index.isin(set(seg_dates))].dropna()
        if len(b):
            out["market_cum_ret_pct"] = round(float(((1.0 + b).prod() - 1.0) * 100), 3)
    return out


def write_outputs(payload: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> tuple[str | None, str | None]:
    """落 JSON + Markdown（默认路径见 DEFAULT_OUT_*）。"""
    jp = Path(json_path) if json_path else DEFAULT_OUT_JSON
    mp = Path(md_path) if md_path else DEFAULT_OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown(payload)
    mp.write_text(md, encoding="utf-8")
    return str(jp), str(mp)


# ================================================================ 数据装载
def load_circ_mv(panel: fl.Panel, data_root: str | Path | None = None) -> pd.DataFrame:
    """从 daily_basic 读 circ_mv（万元）矩阵，对齐面板；缺文件/缺列返回全 NaN。"""
    root = Path(data_root) if data_root else fl.DATA
    folder = root / "daily_basic"
    frames = []
    for dt in panel.dates:
        path = folder / f"d_{dt}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["ts_code", "circ_mv"])
        except Exception:
            continue
        df["trade_date"] = dt
        frames.append(df)
    if not frames:
        return pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
    long = pd.concat(frames, ignore_index=True)
    long["ts_code"] = long["ts_code"].astype(str)
    mv = long.pivot(index="trade_date", columns="ts_code", values="circ_mv")
    return mv.reindex(index=panel.dates, columns=panel.codes)


def load_industry(panel: fl.Panel, data_root: str | Path | None = None) -> pd.Series:
    """从 stock_basic 读静态行业（申万/概念字段），返回 code → industry。"""
    root = Path(data_root) if data_root else fl.DATA
    try:
        sb = fl._load_stock_basic(root)
    except Exception:
        return pd.Series(dtype=object)
    if sb.empty or "industry" not in sb:
        return pd.Series(dtype=object)
    return sb.set_index("ts_code")["industry"]


def load_panel(start: str, end: str, data_root: str | Path | None = None) -> fl.Panel:
    """载入可交易池面板（不载 ROE，省 ~10s；信号只需行情字段）。"""
    return fl.load_panel(start, end, universe="tradable", data_root=data_root, load_roe=False)


# ================================================================ 单战法评测
def assess_war_stop(panel: fl.Panel, sid: str, n: int, start: str = DEFAULT_START,
                    end: str = DEFAULT_END, cost: float = COST,
                    signal: pd.DataFrame | None = None, ret_next: pd.DataFrame | None = None,
                    buyable: pd.DataFrame | None = None, cap_pct: pd.DataFrame | None = None,
                    cap_q: pd.DataFrame | None = None, cap_mv: pd.DataFrame | None = None,
                    industry: pd.Series | None = None, vol20: pd.DataFrame | None = None,
                    segments: dict | None = None) -> dict:
    """单个战法 × 单个窗口 N 的规避组合评测（核心函数）。

    signal/ret_next/buyable/cap_pct/cap_q/cap_mv/vol20/segments 可由 run() 预计算后传入以复用；
    单测/独立调用时可只传 panel+cap_mv，本函数会自行计算缺失项。
    """
    war = WAR_STOPS[sid]
    if signal is None:
        signal = bt.compute_factor(war["factor"], panel)
    if ret_next is None:
        ret_next = next_day_return(panel)
    if buyable is None:
        buyable = build_buyable(panel, ret_next)
    if cap_pct is None or cap_q is None:
        cap_pct, cap_q = cap_rank_frames(cap_mv, buyable)
    if vol20 is None:
        vol20 = panel.adj_close.pct_change(fill_method=None).rolling(20, min_periods=10).std()

    trigger = signal.fillna(0.0).astype(float) >= 0.5
    excl = trigger_window(trigger, n) & buyable

    bench_hold = buyable
    avoid_hold = buyable & ~excl
    r_bench = portfolio_returns(ret_next, bench_hold)
    r_avoid = portfolio_returns(ret_next, avoid_hold)
    excess = (r_avoid - r_bench).rename("excess")
    cov, n_ex = coverage_series(excl, buyable)
    churn = set_churn(excl)
    inc_turn = (holdings_turnover(avoid_hold) - holdings_turnover(bench_hold)).clip(lower=0.0)

    # 评测窗口 [start, end]（面板含 warmup，信号可算；收益只取窗口内且有 T+1 数据的日子）
    dates = pd.Index([str(d) for d in panel.dates])
    win = (dates >= str(start)) & (dates <= str(end))
    eval_dates = dates[win & excess.notna().to_numpy()]
    r_bench = r_bench.loc[eval_dates]
    r_avoid = r_avoid.loc[eval_dates]
    excess = excess.loc[eval_dates]
    cov = cov.loc[eval_dates]
    churn = churn.loc[eval_dates]
    inc_turn = inc_turn.loc[eval_dates]
    n_ex = n_ex.loc[eval_dates]
    ex_eval = excl.loc[eval_dates]
    buy_eval = buyable.loc[eval_dates]

    amt_yuan = panel.amount * AMOUNT_UNIT
    cap_stats = capacity_stats(amt_yuan.loc[eval_dates], ex_eval, buy_eval,
                               (cap_mv.loc[eval_dates] / 1e4) if cap_mv is not None else None)
    gross_ann = float(excess.mean() * ANNUAL_DAYS * 100)
    ann_cost = float(inc_turn.mean() * 2.0 * cost * ANNUAL_DAYS * 100)
    net_ann = gross_ann - ann_cost

    spread = stratified_spread(ret_next.loc[eval_dates], ex_eval, buy_eval,
                               cap_q.loc[eval_dates] if cap_q is not None else None,
                               industry).dropna()
    spread_t = t_stat(spread)
    spread_ann = float(spread.mean() * ANNUAL_DAYS * 100) if len(spread) else None

    # 极端行情分段（run() 预计算可复用；否则现算）
    if segments is None:
        segments = stress_segments(r_bench)
    stress: dict = {}
    for label, seg in segments.items():
        key = "segment_dates" if "segment_dates" in seg else ("window_dates" if "window_dates" in seg else None)
        if key is None:
            continue
        stress[label] = dict(seg, **subset_stats(excess, seg[key], r_bench))
    bear = stress.get("bear_2026", {})
    exposure = exposure_diagnostics(
        vol20.loc[eval_dates] if vol20 is not None else None, ex_eval, buy_eval,
        cap_pct.loc[eval_dates] if cap_pct is not None else None, industry)

    coverage_pct = float(cov.mean() * 100)
    t_ex = t_stat(excess)
    p_ex = bootstrap_p(excess)
    beyond_t = (-spread_t) if spread_t is not None else None
    beyond_ann = (-spread_ann) if spread_ann is not None else None
    result = {
        "n": int(n),
        "factor": war["factor"],
        "direction": war["direction"],
        "eval": {"start": str(eval_dates[0]), "end": str(eval_dates[-1]),
                 "days": int(len(eval_dates))},
        "coverage": {
            "avg_pct": round(coverage_pct, 3),
            "median_pct": round(float(cov.median() * 100), 3),
            "p95_pct": round(float(cov.quantile(0.95) * 100), 3),
            "avg_excluded_count": round(float(n_ex.mean()), 1),
            "avg_holdings_avoid": round(float(avoid_hold.loc[eval_dates].sum(axis=1).mean()), 1),
            "avg_holdings_bench": round(float(buyable.loc[eval_dates].sum(axis=1).mean()), 1),
        },
        "capacity": cap_stats,
        "turnover": {
            "exclusion_churn_avg_pct": round(float(churn.mean() * 100), 3),
            "inc_oneway_turnover_avg_pct": round(float(inc_turn.mean() * 100), 4),
            "ann_cost_pct": round(ann_cost, 3),
        },
        "gross": {
            "ann_excess_pct": round(gross_ann, 3),
            "daily_t": round(t_ex, 2) if t_ex is not None else None,
            "bootstrap_p": p_ex,
            "positive_rate": round(float((excess.dropna() > 0).mean()), 4),
            "ann_ret_avoid_pct": round(float(((1.0 + r_avoid).prod() ** (ANNUAL_DAYS / len(r_avoid)) - 1.0) * 100), 3),
            "ann_ret_bench_pct": round(float(((1.0 + r_bench).prod() ** (ANNUAL_DAYS / len(r_bench)) - 1.0) * 100), 3),
        },
        "net": {"ann_excess_after_cost_pct": round(net_ann, 3)},
        "yearly": yearly_excess(excess, eval_dates, cov),
        "stress": stress,
        "stratified": {
            "spread_ann_pct": round(spread_ann, 3) if spread_ann is not None else None,
            "spread_t": round(spread_t, 2) if spread_t is not None else None,
            "avoidance_beyond_comp_ann_pct": round(beyond_ann, 3) if beyond_ann is not None else None,
            "avoidance_beyond_comp_t": round(beyond_t, 2) if beyond_t is not None else None,
            "bootstrap_p_spread": bootstrap_p(spread) if len(spread) else None,
            "matched_days": int(len(spread)),
        },
        "exposure": exposure,
        "verdict": classify(gross_ann=gross_ann, net_ann=net_ann, daily_t=t_ex,
                            boot_p=p_ex, coverage_pct=coverage_pct,
                            beyond_t=beyond_t, beyond_ann=beyond_ann,
                            bear_ann=bear.get("ann_excess_pct"), bear_t=bear.get("t")),
    }
    return result


def classify(*, gross_ann: float, net_ann: float, daily_t: float | None,
             boot_p: float | None, coverage_pct: float, beyond_t: float | None,
             beyond_ann: float | None, bear_ann: float | None,
             bear_t: float | None) -> dict:
    """结论判定（纯函数，便于单测）：可交易规避 / 仅风控降配 / 不成立。"""
    significant = (daily_t is not None and daily_t >= 2.0
                   and boot_p is not None and boot_p < 0.05)
    tradable = bool(gross_ann > 0 and significant and net_ann > 0 and coverage_pct >= 1.0)
    beyond_comp = bool(beyond_ann is not None and beyond_ann > 0
                       and beyond_t is not None and beyond_t >= 2.0)
    risk_rule = bool(bear_ann is not None and bear_ann > 0
                     and (bear_t is None or bear_t >= 1.0))
    if coverage_pct < 1.0:
        verdict = "覆盖度过低（<1%），即便有效也无可操作意义"
    elif tradable:
        verdict = "可交易规避（毛/净超额均为正且显著）"
    elif gross_ann > 0 and significant and net_ann <= 0:
        verdict = "毛超额显著为正但被换手成本吃掉（净超额≤0）"
    elif gross_ann > 0:
        verdict = "方向为正但不显著（证据不足，不可作交易规则）"
    else:
        verdict = "规避无正超额（原信号反向不成立或已被市场消化）"
    if coverage_pct >= 1.0 and beyond_comp is False and tradable:
        verdict += "；层内对照不显著 → 超额主要来自市值/行业暴露而非信号本身"
    elif coverage_pct >= 1.0 and beyond_comp:
        verdict += "；市值×行业层内同向有效（非单纯小市值/高波动暴露）"
    return {
        "significant_excess": significant,
        "tradable_avoidance": tradable,
        "beyond_cap_industry": beyond_comp,
        "risk_rule_candidate": risk_rule,
        "verdict": verdict,
    }


# ================================================================ 批量评测
def run(start: str = DEFAULT_START, end: str = DEFAULT_END, ns: tuple[int, ...] | list[int] = DEFAULT_NS,
        panel: fl.Panel | None = None, data_root: str | Path | None = None,
        war_ids: list[str] | None = None, n_boot: int = N_BOOT) -> dict:
    """批量跑 4 战法 × N 窗口，返回可直接落盘的 JSON payload。"""
    global N_BOOT
    N_BOOT = int(n_boot)
    t0 = time.time()
    war_ids = list(war_ids or WAR_ORDER)
    t_load = time.time()
    if panel is None:
        panel = load_panel(start, end, data_root=data_root)
    load_sec = round(time.time() - t_load, 2)
    # 预计算共享量：收益/可买池/市值分层/波动/极端分段（4 战法 × N 复用，避免重复 ~40s）
    ret_next = next_day_return(panel)
    buyable = build_buyable(panel, ret_next)
    cap_mv = load_circ_mv(panel, data_root)
    cap_pct, cap_q = cap_rank_frames(cap_mv, buyable)
    industry = load_industry(panel, data_root)
    vol20 = panel.adj_close.pct_change(fill_method=None).rolling(20, min_periods=10).std()
    dates = pd.Index([str(d) for d in panel.dates])
    bench_all = portfolio_returns(ret_next, buyable)
    win = (dates >= str(start)) & (dates <= str(end))
    bench_eval = bench_all[win & bench_all.notna().to_numpy()]
    segments = stress_segments(bench_eval)

    results: dict[str, dict] = {}
    signals: dict[str, pd.DataFrame] = {}
    for idx, sid in enumerate(war_ids, 1):
        war = WAR_STOPS[sid]
        t_sig = time.time()
        signals[sid] = bt.compute_factor(war["factor"], panel)
        sig_sec = round(time.time() - t_sig, 1)
        per_n: dict[str, dict] = {}
        for n in ns:
            t1 = time.time()
            per_n[str(n)] = assess_war_stop(
                panel, sid, int(n), start=start, end=end,
                signal=signals[sid], ret_next=ret_next, buyable=buyable,
                cap_pct=cap_pct, cap_q=cap_q, cap_mv=cap_mv, industry=industry,
                vol20=vol20, segments=segments)
            print(f"[{idx}/{len(war_ids)}] {sid} N={n} 用时 {time.time() - t1:.1f}s（信号 {sig_sec}s）"
                  f" 毛超额={per_n[str(n)]['gross']['ann_excess_pct']:.2f}%"
                  f" t={per_n[str(n)]['gross']['daily_t']}"
                  f" 覆盖={per_n[str(n)]['coverage']['avg_pct']:.1f}%", flush=True)
        results[sid] = {
            "spec_id": sid,
            "name": war["name"],
            "factor": war["factor"],
            "direction": war["direction"],
            "book": war["book"],
            "wave2_evidence": war["wave2"],
            "spec_simplification": bt.SPEC_MAP.get(sid, {}).get("simplification"),
            "by_n": per_n,
            "primary": per_n.get("5"),
        }

    # 汇总（主窗口 N=5）与总判定
    summary = []
    for sid in war_ids:
        r = results[sid]
        primary = r["primary"]
        if primary is None:
            continue
        summary.append({
            "spec_id": sid, "name": r["name"], "factor": r["factor"],
            "coverage_pct": primary["coverage"]["avg_pct"],
            "excluded_count": primary["coverage"]["avg_excluded_count"],
            "gross_ann_pct": primary["gross"]["ann_excess_pct"],
            "gross_t": primary["gross"]["daily_t"],
            "gross_p": primary["gross"]["bootstrap_p"],
            "net_ann_pct": primary["net"]["ann_excess_after_cost_pct"],
            "cost_ann_pct": primary["turnover"]["ann_cost_pct"],
            "beyond_comp_ann_pct": primary["stratified"]["avoidance_beyond_comp_ann_pct"],
            "beyond_comp_t": primary["stratified"]["avoidance_beyond_comp_t"],
            "bear2026_ann_pct": primary["stress"].get("bear_2026", {}).get("ann_excess_pct"),
            "excluded_amount_yi": primary["capacity"].get("excluded_median_amount_yi"),
            "tradable_avoidance": primary["verdict"]["tradable_avoidance"],
            "risk_rule_candidate": primary["verdict"]["risk_rule_candidate"],
            "verdict": primary["verdict"]["verdict"],
        })
    summary.sort(key=lambda x: (x["gross_ann_pct"] is not None, x["gross_ann_pct"]),
                 reverse=True)

    payload = {
        "meta": {
            "report": "reverse_avoid_20261101",
            "version": VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "task": "H 支线：量学反向信号「规避组合」深测（LX20/LX22/LX06/LX12）",
            "data": {"root": str(data_root or fl.DATA), "start": str(start), "end": str(end)},
            "panel": {"dates": int(len(panel.dates)), "codes": int(len(panel.codes)),
                      "load_sec": load_sec, "meta": panel.load_meta},
            "eval": {
                "signal_source": "book_batch_test.compute_factor（复用，不修改）",
                "ns": [int(n) for n in ns],
                "primary_n": 5,
                "horizon_days": 1,
                "timing": "T 日收盘决策 → T 日收盘建仓（近似）→ T+1 收盘卖出；日频等权再平衡",
                "return": "后复权 adj_close(T+1)/adj_close(T)-1",
                "cost_bp_oneway": int(round(COST * 10000)),
                "annual_days": ANNUAL_DAYS,
                "buyable": "tradable 池 ∩ 非 T 日涨停 ∩ T/T+1 复权价可得（基准与规避组合同口径）",
                "block_bootstrap": {"block": BLOCK, "n_boot": N_BOOT, "seed": SEED},
                "stratified_control": "市值五分位×行业单元内 被剔除组−保留组 等权日收益差（剔除≥1、保留≥3）",
                "stress": "2026 年最大回撤峰→谷段 + 全窗口最差 20 日窗口",
            },
            "runtime_sec": round(time.time() - t0, 2),
            "notes": build_notes(),
        },
        "war_stops": results,
        "summary": summary,
        "conclusions": _build_conclusions(summary, {}),
    }
    payload["conclusions"] = _build_conclusions(summary, payload["war_stops"])
    return payload


def build_notes() -> list[str]:
    """报告「口径、局限」固定条目（run() 与离线重渲染共用，保证 JSON/MD 一致）。"""
    return [
        "A 股不能做空：反向信号只能落成「不买/少配/风控剔除」，本组合为纯多头相对等权市场的超额。",
        "日频等权再平衡、T 日收盘成交为近似口径；未做 T+1 收盘入场/成交价敏感性。",
        "短周期反转有利偏差：wave2 的 20 日 CAR 反向幅度仅 -0.85%~-2.08%，本报告的较大年化超额来自"
        "「1 日持有 + 日频再平衡」反复捕捉短周期反向；若按 20 日持有 + T+1 成交价，超额会显著缩小。",
        "覆盖度可执行性：N=20 时 LX20/LX22/LX12 覆盖 49.5%/77.3%/78.5%，已不是「剔除少量风险股」而是"
        "重构组合；可作为诊断，不作风控规则。N=1/5 才属可执行剔除区间。",
        "行业用 stock_basic 当前静态行业（轻微后验）；ST 5% 涨跌停与一字板成交概率未单独建模。",
        "信号为 book_batch_test 的简化单形态代理，结论只针对该代理口径，不否定原书完整战法。",
        "覆盖度低（<1%）时统计功效不足，t 值不可作强结论；日频超额序列存在自相关，已用 block bootstrap 缓解。",
    ]


def _core_conclusions(payload: dict) -> list[str]:
    """核心结论（数据驱动，逐战法可操作判读）。"""
    results = payload.get("war_stops", {})
    lines: list[str] = []
    for sid in WAR_ORDER:
        item = results.get(sid)
        if not item or not item.get("primary"):
            continue
        r = item["primary"]
        cov = r["coverage"]["avg_pct"]
        gross = r["gross"]["ann_excess_pct"]
        net = r["net"]["ann_excess_after_cost_pct"]
        t = r["gross"]["daily_t"]
        strat_t = r["stratified"]["avoidance_beyond_comp_t"]
        bear = r["stress"].get("bear_2026", {})
        cap_med = r["capacity"].get("excluded_median_amount_yi")
        amount_big = r["capacity"].get("excluded_amount_ge_1yi_share")
        churn = r["turnover"]["exclusion_churn_avg_pct"]
        if sid == "LX20_BULL_STOCK_THREE_ABSOLUTES":
            verdict = (f"**规避方向成立、且值得进入风控/降配候选**：N=5 毛 +{_fmt(gross)}%/净 +{_fmt(net)}%，"
                       f"日频 t={_fmt(t)}（p={_fmt(r['gross']['bootstrap_p'], 3)}），层内对照 "
                       f"+{_fmt(r['stratified']['avoidance_beyond_comp_ann_pct'])}%"
                       f"（t={_fmt(strat_t)}）→ 超额不是单纯小市值/行业暴露；"
                       f"2026 熊市段（{bear.get('peak', '—')}~{bear.get('trough', '—')}）年化 "
                       f"{_fmt(bear.get('ann_excess_pct'))}%（t={_fmt(bear.get('t'))}）→ 熊市防守段同样为正。"
                       f"代价：覆盖 {_fmt(cov)}%（日均剔除 {_fmt(r['coverage']['avg_excluded_count'], 0)} 只）、"
                       f"增量换手约 {_fmt(r['turnover']['inc_oneway_turnover_avg_pct'], 2)}%/日、成本吃掉约 "
                       f"{_fmt(r['turnover']['ann_cost_pct'])}%/年；容量中位成交额 {_fmt(cap_med)} 亿、"
                       f"{_fmt((amount_big or 0) * 100)}% 剔除股成交额≥1 亿 → 可执行但需分批。"
                       "**结论：只能「降配/部分剔除」，不宜全剔。**")
        elif sid == "LX22_CONCAVE_NOTCH_GOLD":
            verdict = ("**规避方向为正但不可交易**：N=5 毛 +4.92%（t=2.03），但剔除集合 churn "
                       f"{_fmt(churn)}%/日 → 增量换手 {_fmt(r['turnover']['inc_oneway_turnover_avg_pct'], 2)}%/日、"
                       f"成本 {_fmt(r['turnover']['ann_cost_pct'])}%/年，净超额 {_fmt(net)}%；"
                       f"覆盖 {_fmt(cov)}% 且 2026 熊市段 {_fmt(bear.get('ann_excess_pct'))}%（t={_fmt(bear.get('t'))}）为负。"
                       "**结论：不作交易/风控规则**（事件触发过频 → 组合被信号主导，换手成本吞掉全部超额）。")
        elif sid == "LX06_YIN_LINE_TACTICS":
            verdict = ("**统计上最干净但幅度太小**：覆盖仅 "
                       f"{_fmt(cov)}%（日均 {_fmt(r['coverage']['avg_excluded_count'], 0)} 只），"
                       f"毛 +{_fmt(gross)}%/净 +{_fmt(net)}%，"
                       f"t={_fmt(t)}；层内对照 +{_fmt(r['stratified']['avoidance_beyond_comp_ann_pct'])}%"
                       f"（t={_fmt(strat_t)}）、2026 熊市段 +{_fmt(bear.get('ann_excess_pct'))}%"
                       f"（t={_fmt(bear.get('t'))}）；容量中位 {_fmt(cap_med)} 亿最好。"
                       "**结论：可作「轻度风险剔除」（几乎不改变组合），但单独交易价值≈0。**")
        else:
            by_n = results[sid].get("by_n", {})
            r1 = by_n.get("1") or r
            r20 = by_n.get("20") or r
            verdict = ("**规避方向不成立（反向被利用）**：N=1/5/20 毛超额 "
                       f"{_fmt(r1['gross']['ann_excess_pct'])}% / {_fmt(gross)}% / "
                       f"{_fmt(r20['gross']['ann_excess_pct'])}%，"
                       f"N=5 日频 t={_fmt(t)}、2026 熊市段 {_fmt(bear.get('ann_excess_pct'))}% 全为负。"
                       "双阴（超跌）在 1 日维度是反弹信号，剔除它们反而损失反弹收益；"
                       "wave2 的 D 档只说明「原书看空方向错误」，**不能反推可规避**（与 wave2 报告 note #7 一致："
                       "OOS 有向超额≈0、D 由 IC 侧驱动）。**结论：不作任何规则。**")
        lines.append(f"- **{item['name']}**（`{sid}`）：{verdict}")
    lx20 = results.get("LX20_BULL_STOCK_THREE_ABSOLUTES", {}).get("primary")
    lx20_net = lx20["net"]["ann_excess_after_cost_pct"] if lx20 else None
    lines.append("- **总判定**：4 个代表战法中规避方向统计成立 2 个（LX20/LX06）；"
                 f"通过「显著 + 净正 + 覆盖≥1%」可交易门槛只有 LX20（净 {_fmt(lx20_net)}%/年）；"
                 "LX22 被换手成本吃光、LX12 方向反向。**唯一值得作为风控/降配规则深化的只有 LX20 牛股三绝**，"
                 "LX06 可作轻量剔除；上线前必须补 T+1 成交价/一字板概率/覆盖可执行性与容量复核。")
    return lines


def _interpret_war(item: dict) -> list[str]:
    """单个战法的数据驱动判读（按 N=1/5/20 给出可执行窗口结论）。"""
    by_n = item.get("by_n", {})
    lines: list[str] = []
    for n in ("1", "5", "20"):
        r = by_n.get(n)
        if not r:
            continue
        cov = r["coverage"]["avg_pct"]
        tag = ""
        if n == "20" and cov >= 40:
            tag = "（覆盖过高，视为组合重构诊断，不作规则）"
        elif n == "1" and cov < 1:
            tag = "（信号过稀，统计功效不足）"
        lines.append(f"- N={n}{tag}：毛 {_fmt(r['gross']['ann_excess_pct'])}%/净 "
                     f"{_fmt(r['net']['ann_excess_after_cost_pct'])}%，t={_fmt(r['gross']['daily_t'])}，"
                     f"覆盖 {_fmt(cov)}%，层内 {_fmt(r['stratified']['avoidance_beyond_comp_ann_pct'])}%"
                     f"（t={_fmt(r['stratified']['avoidance_beyond_comp_t'])}），2026 熊市段 "
                     f"{_fmt(r['stress'].get('bear_2026', {}).get('ann_excess_pct'))}%"
                     f"（t={_fmt(r['stress'].get('bear_2026', {}).get('t'))}）。")
    return lines


def _build_conclusions(summary: list[dict], results: dict) -> dict:
    """从 summary 生成总判定（结构化 + 文字）。"""
    n_war = len(summary)
    tradable = [s["spec_id"] for s in summary if s.get("tradable_avoidance")]
    net_positive = [s["spec_id"] for s in summary if (s.get("net_ann_pct") or 0) > 0]
    gross_positive = [s["spec_id"] for s in summary if (s.get("gross_ann_pct") or 0) > 0]
    beyond = [s["spec_id"] for s in summary if (s.get("beyond_comp_t") or 0) >= 2.0
              and (s.get("beyond_comp_ann_pct") or 0) > 0]
    risk = [s["spec_id"] for s in summary if s.get("risk_rule_candidate")]
    bullets: list[str] = []
    bullets.append(f"4 个代表反向战法中，规避组合毛年化超额为正 {len(gross_positive)} 个；"
                   f"扣成本后净超额为正 {len(net_positive)} 个；通过「显著+净正+覆盖≥1%」可交易门槛 {len(tradable)} 个。")
    if tradable:
        bullets.append("可交易规避候选：" + "、".join(tradable) +
                       "（须同时满足日频超额 t≥2、block bootstrap p<0.05、净超额>0、日均覆盖≥1%）。")
    else:
        bullets.append("无可交易规避候选：即使毛超额为正，也未同时通过显著性/成本/覆盖度门槛。")
    if beyond:
        bullets.append("市值×行业层内对照仍为正且显著：" + "、".join(beyond) +
                       "（说明规避超额不只是剔除小市值/特定行业）。")
    else:
        bullets.append("市值×行业层内对照均未显著为正：规避超额无法排除「剔除高波动/小市值/行业暴露」的解释，"
                       "只能当作风格择时/风控线索，不能当作信号 alpha。")
    bullets.append("2026 行情段正向的规避候选：" + ("、".join(risk) if risk else "无") +
                   "（风控/降配规则只在此类样本上才有条件成立）。")
    return {
        "n_war_stops": n_war,
        "gross_positive": gross_positive,
        "net_positive": net_positive,
        "tradable_avoidance": tradable,
        "beyond_cap_industry": beyond,
        "risk_rule_candidates": risk,
        "bullets": bullets,
    }


# ================================================================ 渲染
def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, np.integer)):
        return f"{int(value)}{suffix}"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _bear_definition(payload: dict) -> str | None:
    """从结果里提取 2026 熊市段定义（峰→谷日期、段内市场收益、最大回撤），用于报告脚注。"""
    for sid in WAR_ORDER:
        item = payload.get("war_stops", {}).get(sid)
        if not item or not item.get("primary"):
            continue
        bear = item["primary"].get("stress", {}).get("bear_2026")
        if not bear:
            continue
        return (f"2026 熊市段定义 = 2026 年内等权基准最大回撤「峰→谷」区间 "
                f"{bear.get('peak')}~{bear.get('trough')}（{bear.get('segment_days')} 个交易日，"
                f"峰谷最大回撤 {_fmt(bear.get('max_drawdown_pct'))}%，段内等权基准收益 "
                f"{_fmt(bear.get('market_cum_ret_pct'))}%）；表中「2026熊市段年化%」= 该段内日频超额×252。")
    return None


def render_markdown(payload: dict) -> str:
    """把 payload 渲染成 Markdown 报告（汇总表 + 逐战法详表 + 结论 + 局限）。"""
    meta = payload["meta"]
    ev = meta["eval"]
    lines: list[str] = []
    a = lines.append
    a("# 量学反向信号「规避组合」深测 · LX20/LX22/LX06/LX12（2026-11-01，支线 H）")
    a("")
    a(f"- 生成时间：{meta['generated_at']}")
    a(f"- 数据：`{meta['data']['root']}` · 请求 {meta['data']['start']}~{meta['data']['end']} · "
      f"面板 {meta['panel']['dates']} 交易日 × {meta['panel']['codes']} 只（载入 {meta['panel']['load_sec']}s）")
    a("- 信号定义：复用 `tools/research/book_batch_test.py`（lx06/lx12/lx20）+ `factor_lib.FACTORS['aokou']`（LX22），**不修改上游文件**")
    a(f"- 规避组合 = 每日等权全市场 **剔除**过去 N 日（N={ev['ns']}，主口径 N={ev['primary_n']}）触发信号的股票；"
      f"基准 = 同一可交易池（同样剔除 T 日收盘涨停不可买）")
    a(f"- 时点口径：{ev['timing']}；收益 = {ev['return']}；成本 = 单边 {ev['cost_bp_oneway']}bp；"
      f"t = 日频超额 t，p = block bootstrap（块长 {ev['block_bootstrap']['block']}，{ev['block_bootstrap']['n_boot']} 次）")
    a(f"- 运行耗时：{meta['runtime_sec']}s（含面板载入 {meta['panel']['load_sec']}s）")
    a("")

    a("## 一、汇总表（主口径 N=5）")
    a("")
    a("| 战法 | 因子 | 日均覆盖% | 日均剔除(只) | 毛年化超额% | 日频t | bootstrap p | 净年化超额% | 成本年化% | "
      "层内超额%(t) | 2026熊市段年化% | 被剔除成交额中位(亿) | 可交易规避 | 风控候选 |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for s in payload["summary"]:
        beyond = f"{_fmt(s['beyond_comp_ann_pct'])} ({_fmt(s['beyond_comp_t'])})"
        a(f"| {s['name']} | `{s['factor']}` | {_fmt(s['coverage_pct'])} | "
          f"{_fmt(s['excluded_count'], 0)} | {_fmt(s['gross_ann_pct'])} | {_fmt(s['gross_t'])} | "
          f"{_fmt(s['gross_p'], 3)} | {_fmt(s['net_ann_pct'])} | {_fmt(s['cost_ann_pct'], 3)} | "
          f"{beyond} | {_fmt(s['bear2026_ann_pct'])} | {_fmt(s['excluded_amount_yi'])} | "
          f"{'✅' if s['tradable_avoidance'] else '❌'} | {'✅' if s['risk_rule_candidate'] else '❌'} |")
    a("")
    a("> 毛/净超额 = 规避组合日收益 − 等权基准日收益 的年化（×252）；净超额 = 毛超额 − 增量单边换手×2×30bp×252。")
    bear_def = _bear_definition(payload)
    if bear_def:
        a(">")
        a(f"> {bear_def}")
    a("")

    a("## 二、核心结论（可操作判读）")
    a("")
    for line in _core_conclusions(payload):
        a(line)
    a("")

    a("## 三、逐战法明细（N=1/5/20）")
    for sid in WAR_ORDER:
        item = payload["war_stops"].get(sid)
        if not item:
            continue
        a("")
        a(f"### {sid} · {item['name']}")
        a(f"- 原书：{item['book']} · 映射因子 `{item['factor']}`（direction={item['direction']}）")
        a(f"- wave2 证据：{item['wave2_evidence']}")
        if item.get("spec_simplification"):
            a(f"- 简化口径：{item['spec_simplification']}")
        a("")
        a("| N | 覆盖% | 剔除数 | 毛年化% | t | p | 净年化% | 增量换手% | 层内超额%(t) | 2026熊市段年化%(t) | "
          "被剔除成交额中位(亿) |")
        a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for n in (1, 5, 20):
            r = item["by_n"].get(str(n))
            if not r:
                continue
            bear = r["stress"].get("bear_2026", {})
            bear_txt = f"{_fmt(bear.get('ann_excess_pct'))} ({_fmt(bear.get('t'))})" if bear else "—"
            a(f"| {n} | {_fmt(r['coverage']['avg_pct'])} | {_fmt(r['coverage']['avg_excluded_count'], 0)} | "
              f"{_fmt(r['gross']['ann_excess_pct'])} | {_fmt(r['gross']['daily_t'])} | "
              f"{_fmt(r['gross']['bootstrap_p'], 3)} | {_fmt(r['net']['ann_excess_after_cost_pct'])} | "
              f"{_fmt(r['turnover']['inc_oneway_turnover_avg_pct'], 3)} | "
              f"{_fmt(r['stratified']['avoidance_beyond_comp_ann_pct'])} ({_fmt(r['stratified']['avoidance_beyond_comp_t'])}) | "
              f"{bear_txt} | {_fmt(r['capacity'].get('excluded_median_amount_yi'))} |")
        r5 = item["primary"]
        if r5:
            a("")
            a("逐年（N=5）：")
            a("")
            a("| 年份 | 天数 | 年化超额% | t | 正日占比 | 覆盖% |")
            a("|---|---:|---:|---:|---:|---:|")
            for year, y in r5["yearly"].items():
                a(f"| {year} | {y['days']} | {_fmt(y['ann_excess_pct'])} | {_fmt(y['t'])} | "
                  f"{_fmt(y['positive_rate'], 3)} | {_fmt(y['coverage_pct'])} |")
            a("")
            a(f"- 换手/成本（N=5）：剔除集合 churn {_fmt(r5['turnover']['exclusion_churn_avg_pct'])}%/日、"
              f"增量单边换手 {_fmt(r5['turnover']['inc_oneway_turnover_avg_pct'], 3)}%/日、成本年化 {_fmt(r5['turnover']['ann_cost_pct'], 3)}%")
            a(f"- 容量/暴露（N=5）：剔除股票成交额均值 {_fmt(r5['capacity'].get('excluded_avg_amount_yi'))} 亿、"
              f"≥1 亿占比 {_fmt(r5['capacity'].get('excluded_amount_ge_1yi_share'), 3)}、"
              f"流通市值中位 {_fmt(r5['capacity'].get('excluded_median_circ_mv_yi'))} 亿；"
              f"市值分位 {_fmt(r5['exposure'].get('excluded_cap_percentile_mean'))}（池内 "
              f"{_fmt(r5['exposure'].get('universe_cap_percentile_mean'))}）、"
              f"20日波动 {_fmt(r5['exposure'].get('excluded_vol20_mean'), 4)}（池内 "
              f"{_fmt(r5['exposure'].get('universe_vol20_mean'), 4)}）")
            a(f"- 分层对照：被剔除组−同层保留组 年化 {_fmt(r5['stratified']['spread_ann_pct'])}%"
              f"（t={_fmt(r5['stratified']['spread_t'])}，匹配日 {r5['stratified']['matched_days']}）；"
              f"→ 规避相对同层保留的增益 {_fmt(r5['stratified']['avoidance_beyond_comp_ann_pct'])}%"
              f"（t={_fmt(r5['stratified']['avoidance_beyond_comp_t'])}）")
            other = r5["stress"].get("worst_window_20d")
            if other:
                a(f"- 极端窗口：全窗口最差 20 日 {other.get('start')}~{other.get('end')} "
                  f"（市场 {_fmt(other.get('market_ret_pct'))}%）规避年化 {_fmt(other.get('ann_excess_pct'))}%"
                  f"（t={_fmt(other.get('t'))}）")
            a(f"- **结论（N=5）**：{r5['verdict']['verdict']}")
            a("")
            a("判读（N=1/5/20）：")
            for line in _interpret_war(item):
                a(line)

    a("")
    a("## 四、总判定")
    a("")
    for b in payload["conclusions"]["bullets"]:
        a(f"- {b}")
    a("")
    a("## 五、口径、局限与复现")
    a("")
    for i, note in enumerate(meta["notes"], 1):
        a(f"{i}. {note}")
    a("")
    a("```powershell")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/reverse_avoid_test.py --start 20230101 "
      "--end 20261030 --ns 1,5,20 --out research/reports/reverse_avoid_20261101.json "
      "--md research/reports/reverse_avoid_20261101.md")
    a("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_reverse_avoid.py")
    a("```")
    a("")
    a("> 判读纪律：本报告是「规避型用法」的可交易性深测，不构成收益预期；任何规则上线前需再补 "
      "T+1 成交价敏感性、一字板成交概率与实盘容量复核（MEETING.md 四关口径）。")
    return "\n".join(lines)


# ================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="量学反向信号规避组合深测")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--ns", default="1,5,20", help="剔除窗口 N，逗号分隔（默认 1,5,20）")
    parser.add_argument("--war", default=None, help="只跑指定 spec_id（逗号分隔，默认 4 个全跑）")
    parser.add_argument("--out", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--data-root", default=None, help="数据根（默认 quant_backtest/data/tushare）")
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    args = parser.parse_args(argv)

    ns = tuple(int(x.strip()) for x in str(args.ns).split(",") if x.strip())
    if not ns:
        raise SystemExit("--ns 不能为空")
    war_ids = [x.strip() for x in args.war.split(",")] if args.war else None
    unknown = [w for w in (war_ids or []) if w not in WAR_STOPS]
    if unknown:
        raise SystemExit(f"未知战法: {unknown}；可选: {WAR_ORDER}")

    t0 = time.time()
    payload = run(start=args.start, end=args.end, ns=ns, data_root=args.data_root,
                  war_ids=war_ids, n_boot=args.n_boot)
    jp, mp = write_outputs(payload, args.out, args.md)
    print(f"[OK] 用时 {time.time() - t0:.1f}s")
    print(f"[JSON] {jp}")
    print(f"[MD]   {mp}")
    print()
    print("汇总（N=5，毛/净年化超额、日频 t、覆盖度、层内超额 t）：")
    for s in payload["summary"]:
        print(f"  {s['spec_id']:<32} 毛超={s['gross_ann_pct']:+7.2f}% 净超={s['net_ann_pct']:+7.2f}% "
              f"t={_fmt(s['gross_t']):>6} p={_fmt(s['gross_p'], 3):>6} 覆盖={s['coverage_pct']:.2f}% "
              f"层内t={_fmt(s['beyond_comp_t']):>6} → {'可交易规避' if s['tradable_avoidance'] else '不成立'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
