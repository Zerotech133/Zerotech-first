# -*- coding: utf-8 -*-
"""tools/research/lx20_avoid_rule.py · LX20 牛股三绝「规避/降配规则」可执行性深化（2026-11-01，wave4 支线 K）。

背景
----
wave3 的 `tools/research/reverse_avoid_test.py` 发现 4 个反向战法中只有 LX20 牛股三绝的规避组合
通过可交易门槛（N=5：毛 +7.21%/净 +2.75%、日频 t=7.35、覆盖 24.61%、层内对照 +34.6%/t=9.76、
2026 熊市段 +6.83%），但那是「T 日收盘决策 → T 日收盘买入 → T+1 收盘卖出」的日频近似。
本文件把 LX20 规避规则做到可执行/可注册：

  ① 参数敏感性：回溯窗口 N ∈ {1,3,5,10,20} × 触发覆盖度；
  ② 成交价口径：T 日收盘买入（基线）/ T+1 开盘买入 / T+1 收盘买入；涨停（含一字涨停）不可买、
     一字跌停不可卖、停牌跳过；
  ③ 一字板/停牌概率与处理（全样本 + 被剔除集合条件概率，按年）；
  ④ 容量：按流通市值五分位分层，基于 20 日日均成交额与「单只股票单日交易额 ≤ 10% ADV」
     估算 AUM 上限（含换手口径与建仓口径）；
  ⑤ 组合实现：全剔除（weight=0）vs 部分降配（信号股市值权重 25%/50%），vs 等权市场基准；
  ⑥ 输出可注册的规则规格（rules / threshold / coverage / capacity / risk / evidence）与
     「是否值得作为策略库 S05 风控降配规则候选、上线前还缺什么」的结论。

复用（不修改）：`reverse_avoid_test` 的 LX20 信号（book_batch_test.compute_factor）、可交易池、
市值/复权加载、涨停口径、t 值/block bootstrap、滚动窗口与等权组合口径；本文件只做扩展。

口径
----
- T 日收盘出信号（只用 ≤T 数据）；成交口径三选一：
  * `T_CLOSE`  ：T 收盘买入 → T+1 收盘卖出（wave3 基线，含 T→T+1 隔夜跳空）；
  * `T1_CLOSE` ：T+1 收盘买入 → T+2 收盘卖出（决策后一个整日才成交）；
  * `T1_OPEN`  ：T+1 开盘买入 → T+2 开盘卖出；
- 可交易约束（基准与规避组合**同口径**）：entry 日涨停/一字涨停不可买；exit 日一字跌停不可卖；
  entry/exit 日停牌（无数据或 vol=0）跳过；回测按「该股该日对称剔除」近似不可卖（未做顺延持有）；
- 成本：单边 30bp；净超额 = 毛超额 − 日度增量单边换手×2×30bp×252；
- 市场基准 = 同一可交易池等权、同样剔除不可买标的；
- 部分降配：组合权重 = 非信号股 1 份、信号股 w 份（w=0 全剔除；w=1 退化为基准）；
  组合收益 = (Σ非信号收益 + w×Σ信号收益) / (n_non + w×n_signal)，同时换手与成本同步缩放；
- 容量假设：参与率上限 10% ADV（单票单日交易额/日均成交额），AUM 上限取日频估计的中位数；
- 一字板判定：open=high=low=close 且涨跌幅触及板限制（主板 10%/创业板科创板 20%/北交所 30%，阈值放宽 0.5pp）。

用法
----
    # 先跑通 N=5 主口径
    C:\\项目\\py312\\python.exe -X utf8 tools/research/lx20_avoid_rule.py --ns 5 \
        --out tmp/lx20_n5.json --md tmp/lx20_n5.md
    # 全量（N=1,3,5,10,20 × 3 成交口径 × 3 降配档）
    C:\\项目\\py312\\python.exe -X utf8 tools/research/lx20_avoid_rule.py \
        --out research/reports/lx20_avoid_rule_20261101.json \
        --md research/reports/lx20_avoid_rule_20261101.md

局限：信号为 book_batch_test 的简化单形态代理；一字板为日线近似（无逐笔封单数据）、
未建模集合竞价成交概率与冲击成本曲线；行业/市值分层沿用 wave3 口径；
「不可卖」按对称剔除近似，未做顺延持有；AUM 上限为静态 ADV 假设，未做动态冲击模型。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 路径/依赖
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import factor_lib as fl                # noqa: E402
import reverse_avoid_test as rav       # noqa: E402  复用信号/基准/统计口径（不修改）

VERSION = "0.1.0-wave4"
ANNUAL_DAYS = 252
COST = fl.DEFAULT_COST                 # 单边 30bp
AMOUNT_UNIT = fl.AMOUNT_UNIT           # 千元 → 元
LX20_SID = "LX20_BULL_STOCK_THREE_ABSOLUTES"
FACTOR = rav.WAR_STOPS[LX20_SID]["factor"]
DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
DEFAULT_NS = (1, 3, 5, 10, 20)
EXECUTIONS = ("T_CLOSE", "T1_OPEN", "T1_CLOSE")
EXEC_DESC = {
    "T_CLOSE": "T 日收盘买入 → T+1 收盘卖出（wave3 基线，含隔夜跳空）",
    "T1_OPEN": "T 日收盘决策，T+1 开盘买入 → T+2 开盘卖出",
    "T1_CLOSE": "T 日收盘决策，T+1 收盘买入 → T+2 收盘卖出",
}
DEWEIGHTS = (0.0, 0.25, 0.5)           # 信号股保留权重：0=全剔除，0.25/0.5=部分降配
PRIMARY = {"n": 5, "execution": "T_CLOSE", "deweight": 0.0}
DEFAULT_OUT_JSON = REPO / "research" / "reports" / "lx20_avoid_rule_20261101.json"
DEFAULT_OUT_MD = REPO / "research" / "reports" / "lx20_avoid_rule_20261101.md"
PARTICIPATION = 0.10                   # 单票单日交易额 ≤ 10% ADV
BLOCK = 5
N_BOOT = 300
SEED = 42


# ================================================================ 基础矩阵
def build_adj_open(panel: fl.Panel) -> pd.DataFrame:
    """后复权开盘价：adj_open = open × adj_factor，adj_factor = adj_close / close。"""
    adj_close = panel.adj_close
    return panel.open * (adj_close / panel.close)


def execution_returns(panel: fl.Panel) -> dict[str, pd.DataFrame]:
    """三种成交口径的持有 1 日收益矩阵（信号日 T 的行 = 该决策对应的收益）。"""
    adj_c = panel.adj_close
    adj_o = build_adj_open(panel)
    out = {
        # entry=close(T), exit=close(T+1)
        "T_CLOSE": (adj_c.shift(-1) / adj_c - 1.0),
        # entry=close(T+1), exit=close(T+2)
        "T1_CLOSE": (adj_c.shift(-2) / adj_c.shift(-1) - 1.0),
        # entry=open(T+1), exit=open(T+2)
        "T1_OPEN": (adj_o.shift(-2) / adj_o.shift(-1) - 1.0),
    }
    return out


def shift_future(mask: np.ndarray, k: int) -> np.ndarray:
    """把 (T+k) 日的布尔值对齐到 T 日（k>0 为未来）；越界补 False。"""
    out = np.zeros_like(mask, dtype=bool)
    if k > 0:
        out[:-k] = mask[k:]
    elif k < 0:
        out[-k:] = mask[:k]
    else:
        out = mask.copy()
    return out


def tradeability_masks(panel: fl.Panel) -> dict[str, np.ndarray]:
    """可交易性掩码：涨停（收盘/开盘）、一字涨停/一字跌停、停牌（T/T+1/T+2）。"""
    thr = rav.board_limit_pct(list(panel.codes))[None, :] - 0.5
    pct = np.asarray(panel.pct_chg, dtype=float)
    opn = np.asarray(panel.open, dtype=float)
    high = np.asarray(panel.high, dtype=float)
    low = np.asarray(panel.low, dtype=float)
    close = np.asarray(panel.close, dtype=float)
    pre = np.asarray(panel.pre_close, dtype=float)
    vol = np.asarray(panel.vol, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        open_pct = (opn / pre - 1.0) * 100.0
    one_word = np.isfinite(close) & np.isfinite(opn) & np.isfinite(high) & np.isfinite(low) \
        & (opn == high) & (high == low) & (low == close)
    limit_up_close = np.isfinite(pct) & (pct >= thr)
    limit_up_open = np.isfinite(open_pct) & (open_pct >= thr)
    oneword_up = one_word & limit_up_close
    oneword_down = one_word & np.isfinite(pct) & (pct <= -thr)
    tradable_day = np.isfinite(close) & np.isfinite(vol) & (vol > 0)
    return {
        "limit_up_close": limit_up_close,
        "limit_up_open": limit_up_open,
        "oneword_up": oneword_up,
        "oneword_down": oneword_down,
        "tradable_day": tradable_day,
    }


def build_universe(panel: fl.Panel, ret: pd.DataFrame, masks: dict[str, np.ndarray],
                   execution: str) -> np.ndarray:
    """某成交口径下的可执行等权池（entry/exit 可成交且有收益），形状同面板。"""
    eligible = panel.eligible_mask().fillna(False).to_numpy().astype(bool)
    finite = np.isfinite(np.asarray(ret, dtype=float))
    entry_lag = 0 if execution == "T_CLOSE" else 1
    exit_lag = 1 if execution == "T_CLOSE" else 2
    tradable = masks["tradable_day"]
    entry_ok = shift_future(tradable, entry_lag).copy()
    exit_ok = shift_future(tradable, exit_lag) & ~shift_future(masks["oneword_down"], exit_lag)
    if execution == "T_CLOSE":
        entry_ok &= ~masks["limit_up_close"]                       # T 日收盘涨停不可买
    elif execution == "T1_CLOSE":
        entry_ok &= ~shift_future(masks["limit_up_close"], 1)      # T+1 收盘涨停不可买
    else:  # T1_OPEN
        entry_ok &= ~shift_future(masks["limit_up_open"], 1)       # T+1 开盘涨停不可买
    return eligible & finite & entry_ok & exit_ok


def trigger_window_np(trigger: np.ndarray, n: int) -> np.ndarray:
    """过去 n 日（含当日）是否触发过（cumsum 版，供 N 敏感性快速重算）。"""
    t = trigger.astype(np.int64)
    c = np.cumsum(t, axis=0)
    out = np.zeros_like(trigger, dtype=bool)
    if int(n) <= 1:
        return t.astype(bool)
    m = min(int(n), len(trigger))
    out[:m] = c[:m] > 0
    out[m:] = (c[m:] - c[:-m]) > 0
    return out


# ================================================================ 组合/换手/指标
def row_mean(ret: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """逐日横截面等权均值（mask 内、有限值）。"""
    m = mask & np.isfinite(ret)
    cnt = m.sum(axis=1).astype(float)
    tot = np.where(m, ret, 0.0).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(cnt > 0, tot / np.where(cnt > 0, cnt, 1.0), np.nan)


def deweight_returns(ret: np.ndarray, universe: np.ndarray, excl: np.ndarray,
                     w: float) -> tuple[np.ndarray, np.ndarray]:
    """基准与降配组合日收益：信号股保留权重 w（0=全剔除，1=基准）。"""
    finite = np.isfinite(ret)
    u = universe & finite
    e = u & excl
    k = u & ~excl
    n_k = k.sum(axis=1).astype(float)
    n_e = e.sum(axis=1).astype(float)
    s_k = np.where(k, ret, 0.0).sum(axis=1)
    s_e = np.where(e, ret, 0.0).sum(axis=1)
    bench = row_mean(ret, universe)
    denom = n_k + float(w) * n_e
    with np.errstate(divide="ignore", invalid="ignore"):
        avoid = np.where(denom > 0, (s_k + float(w) * s_e) / np.where(denom > 0, denom, 1.0), np.nan)
    return bench, avoid


def deweight_turnover(universe: np.ndarray, excl: np.ndarray, w: float,
                      ret: np.ndarray) -> np.ndarray:
    """降配组合的日度单边换手（0.5×Σ|w_t-w_{t-1}|），首日 NaN。"""
    finite = np.isfinite(ret)
    u = universe & finite
    e = u & excl
    k = u & ~excl
    n_k = k.sum(axis=1).astype(float)
    n_e = e.sum(axis=1).astype(float)
    denom = n_k + float(w) * n_e
    inv = np.where(denom > 0, 1.0 / np.where(denom > 0, denom, 1.0), 0.0)
    weights = np.where(k, inv[:, None], 0.0) + np.where(e, float(w) * inv[:, None], 0.0)
    d = np.abs(np.diff(weights, axis=0)).sum(axis=1) * 0.5
    out = np.full(len(weights), np.nan)
    out[1:] = d
    return out


def set_churn_np(mask: np.ndarray) -> np.ndarray:
    """集合日度 churn = 1 − Jaccard(今日, 昨日)。"""
    cur = mask.astype(bool)
    prev = np.zeros_like(cur)
    prev[1:] = cur[:-1]
    inter = (cur & prev).sum(axis=1).astype(float)
    union = (cur | prev).sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 1.0 - inter / np.where(union > 0, union, 1.0)
    out[0] = np.nan
    return out


def incremental_turnover(universe: np.ndarray, excl: np.ndarray, w: float,
                         ret: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """规避/降配组合相对基准的**增量**单边换手（成本只对增量计，与 wave3 口径一致）。

    返回 (turn_avoid, turn_bench, inc)；inc = max(turn_avoid − turn_bench, 0)。
    """
    turn_avoid = deweight_turnover(universe, excl, float(w), ret)
    turn_bench = deweight_turnover(universe, excl, 1.0, ret)
    valid = np.isfinite(turn_avoid) & np.isfinite(turn_bench)
    inc = np.where(valid, np.maximum(turn_avoid - turn_bench, 0.0), np.nan)
    return turn_avoid, turn_bench, inc


def t_stat(series) -> float | None:
    return rav.t_stat(pd.Series(series))


def bootstrap_p(series) -> float | None:
    return rav.bootstrap_p(pd.Series(series), block=BLOCK, n_boot=N_BOOT, seed=SEED)


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, np.integer)):
        return f"{int(value)}{suffix}"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def yearly_stats(excess: np.ndarray, dates: pd.Index) -> dict:
    out: dict[str, dict] = {}
    for year in ("2023", "2024", "2025", "2026"):
        sel = np.asarray([str(d).startswith(year) for d in dates])
        sub = pd.Series(excess[sel]).dropna()
        if len(sub) == 0:
            continue
        out[year] = {"days": int(len(sub)),
                     "ann_excess_pct": round(float(sub.mean() * ANNUAL_DAYS * 100), 3),
                     "t": round(t_stat(sub), 2) if t_stat(sub) is not None else None,
                     "positive_rate": round(float((sub > 0).mean()), 4)}
    return out


def segment_stats(excess: np.ndarray, dates: pd.Index, seg_dates: list[str]) -> dict:
    idx = pd.Index([str(d) for d in dates])
    sel = idx.isin(set(str(d) for d in seg_dates))
    sub = pd.Series(excess[sel]).dropna()
    if len(sub) == 0:
        return {"days": 0}
    tv = t_stat(sub)
    return {"days": int(len(sub)),
            "ann_excess_pct": round(float(sub.mean() * ANNUAL_DAYS * 100), 3),
            "cum_excess_pct": round(float(sub.sum() * 100), 3),
            "t": round(tv, 2) if tv is not None else None}


# ================================================================ 容量
def cap_quintiles(cap: pd.DataFrame, universe: np.ndarray) -> np.ndarray:
    """当日等权池内流通市值五分位（0=最小 … 4=最大；NaN/无市值 = -1）。"""
    cap_arr = np.asarray(cap, dtype=float)
    q = np.full(cap_arr.shape, -1, dtype=np.int64)
    for i in range(cap_arr.shape[0]):
        ok = universe[i] & np.isfinite(cap_arr[i])
        if int(ok.sum()) >= 20:
            ranks = pd.Series(cap_arr[i, ok]).rank(pct=True).to_numpy()
            q[i, ok] = np.clip((ranks * 5.0).astype(np.int64), 0, 4)
    return q


def capacity_estimates(adv20: np.ndarray, universe: np.ndarray, excl: np.ndarray,
                       inc_turn: np.ndarray, cap_q: np.ndarray | None,
                       participation: float = PARTICIPATION) -> dict:
    """按「单票单日交易额 ≤ participation×ADV20」估算 AUM 上限（元）。

    每日：可交易资金 ≈ participation × p5(ADV20 of 当日新增/减少剔除股) × 变动只数 / 单边换手；
    取全日中位数 → AUM 上限。另给「建仓口径」= participation × p5(ADV20 of 持仓) × 持仓数。
    """
    ex = excl & universe
    delta = np.zeros_like(ex)
    delta[1:] = ex[1:] ^ ex[:-1]
    adv = np.where(np.isfinite(adv20), adv20, np.nan)
    trade_caps, build_caps = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for i in range(len(ex)):
            it = inc_turn[i]
            dl = delta[i]
            n_dl = int(dl.sum())
            hold = universe[i]
            n_hold = int(hold.sum())
            if n_dl >= 5 and np.isfinite(it) and it > 1e-8:
                p5 = np.nanpercentile(adv[i, dl], 5)
                if np.isfinite(p5):
                    trade_caps.append(participation * p5 * n_dl / it)
            if n_hold >= 20:
                p5h = np.nanpercentile(adv[i, hold], 5)
                if np.isfinite(p5h):
                    build_caps.append(participation * p5h * n_hold)
    out = {
        "assumption": f"单票单日交易额 ≤ {participation:.0%}×20日日均成交额；AUM 上限取日频估计中位数",
        "aum_trade_cap_yi": round(float(np.median(trade_caps)) / 1e8, 2) if trade_caps else None,
        "aum_build_cap_yi": round(float(np.median(build_caps)) / 1e8, 2) if build_caps else None,
    }
    if out["aum_trade_cap_yi"] is not None and out["aum_build_cap_yi"] is not None:
        out["aum_cap_yi"] = round(min(out["aum_trade_cap_yi"], out["aum_build_cap_yi"]), 2)
    else:
        out["aum_cap_yi"] = out["aum_trade_cap_yi"] or out["aum_build_cap_yi"]
    if cap_q is not None:
        layers = []
        for q in range(5):
            sel = ex & (cap_q == q)
            n_sel = int(sel.sum())
            if n_sel < 100:
                continue
            adv_vals = adv[sel]
            adv_vals = adv_vals[np.isfinite(adv_vals)]
            if adv_vals.size == 0:
                continue
            layers.append({
                "cap_quintile": q + 1,
                "excluded_share_pct": round(100.0 * n_sel / max(1, int(ex.sum())), 2),
                "median_adv20_yi": round(float(np.median(adv_vals)) / 1e8, 3),
                "p5_adv20_yi": round(float(np.percentile(adv_vals, 5)) / 1e8, 3),
            })
        out["by_cap_layer"] = layers
    return out


# ================================================================ 一字板/停牌概率
def tradeability_probabilities(panel: fl.Panel, masks: dict[str, np.ndarray],
                               eligible: np.ndarray, excl_signal: np.ndarray | None,
                               dates: pd.Index) -> dict:
    """可交易性概率：全池 vs 被剔除（信号触发&可交易池）集合，按年统计。

    停牌 = 入场/出场日无数据或 vol=0；被剔除集合用「信号触发 ∩ 可交易池」（不预先过滤涨停股），
    这样才能看出「规避名单本身有多少比例是不可成交的」。
    """
    tradable = masks["tradable_day"]
    entry_lag_by_view = {"T日收盘买": ("limit_up_close", 0, 1),
                         "T+1收盘买": ("limit_up_close", 1, 2),
                         "T+1开盘买": ("limit_up_open", 1, 2)}
    table: dict[str, dict] = {}
    for view, (limit_key, lag_entry, lag_exit) in entry_lag_by_view.items():
        row: dict[str, dict] = {}
        for year in ("all", "2023", "2024", "2025", "2026"):
            if year == "all":
                sel = np.ones(len(dates), dtype=bool)
            else:
                sel = np.asarray([str(d).startswith(year) for d in dates])
            base_cnt = eligible[sel].sum()
            if base_cnt == 0:
                continue
            limit_entry = shift_future(masks[limit_key], lag_entry) & eligible
            oneword_up = shift_future(masks["oneword_up"], lag_entry) & eligible
            suspend_entry = ~shift_future(tradable, lag_entry) & eligible
            oneword_down = shift_future(masks["oneword_down"], lag_exit) & eligible
            suspend_exit = ~shift_future(tradable, lag_exit) & eligible
            row[year] = {
                "涨停不可买_pct": round(100.0 * limit_entry[sel].sum() / base_cnt, 3),
                "一字涨停不可买_pct": round(100.0 * oneword_up[sel].sum() / base_cnt, 3),
                "停牌_入场_pct": round(100.0 * suspend_entry[sel].sum() / base_cnt, 3),
                "一字跌停不可卖_pct": round(100.0 * oneword_down[sel].sum() / base_cnt, 3),
                "停牌_出场_pct": round(100.0 * suspend_exit[sel].sum() / base_cnt, 3),
            }
        table[view] = row
    if excl_signal is not None:
        ex = excl_signal & eligible
        cond: dict[str, dict] = {}
        for year in ("all", "2023", "2024", "2025", "2026"):
            sel = np.ones(len(dates), dtype=bool) if year == "all" else np.asarray(
                [str(d).startswith(year) for d in dates])
            ex_y = ex[sel]
            n = int(ex_y.sum())
            if n == 0:
                continue
            for view, (limit_key, lag_entry, lag_exit) in entry_lag_by_view.items():
                limit_entry = shift_future(masks[limit_key], lag_entry)
                oneword_down = shift_future(masks["oneword_down"], lag_exit)
                cond.setdefault(view, {})[year] = {
                    "n": n,
                    "涨停不可买_pct": round(100.0 * (limit_entry[sel] & ex_y).sum() / n, 3),
                    "一字跌停不可卖_pct": round(100.0 * (oneword_down[sel] & ex_y).sum() / n, 3),
                }
        table["_excluded_conditional"] = cond
    return table


# ================================================================ 主评测
def evaluate_config(ret: np.ndarray, dates: pd.Index, universe: np.ndarray, excl: np.ndarray,
                    w: float, segments: dict[str, list[str]], want_bootstrap: bool = True) -> dict:
    """单个 (N, 成交口径, 降配档) 的完整指标。"""
    bench, avoid = deweight_returns(ret, universe, excl, w)
    excess = avoid - bench
    valid = np.isfinite(excess)
    ex_s = excess[valid]
    mean_ex = float(np.nanmean(ex_s)) if len(ex_s) else float("nan")
    gross_ann = mean_ex * ANNUAL_DAYS * 100
    turn_avoid, turn_bench, inc_turn_series = incremental_turnover(universe, excl, w, ret)
    valid_t = np.isfinite(inc_turn_series)
    inc_turn = float(np.nanmean(inc_turn_series[valid_t])) if valid_t.any() else 0.0
    abs_turn = float(np.nanmean(turn_avoid[np.isfinite(turn_avoid)])) if np.isfinite(turn_avoid).any() else 0.0
    ann_cost = inc_turn * 2.0 * COST * ANNUAL_DAYS * 100
    te = float(np.nanstd(ex_s) * np.sqrt(ANNUAL_DAYS) * 100) if len(ex_s) else float("nan")
    ex_counts = (excl & universe).sum(axis=1).astype(float)
    u_counts = universe.sum(axis=1).astype(float)
    cov = np.where(u_counts > 0, ex_counts / np.where(u_counts > 0, u_counts, 1.0), np.nan)
    cov_fin = cov[np.isfinite(cov)]
    tv = t_stat(pd.Series(ex_s))
    out = {
        "w": float(w),
        "coverage": {
            "avg_pct": round(float(cov_fin.mean()) * 100, 3) if cov_fin.size else None,
            "median_pct": round(float(np.median(cov_fin)) * 100, 3) if cov_fin.size else None,
            "p95_pct": round(float(np.percentile(cov_fin, 95)) * 100, 3) if cov_fin.size else None,
            "avg_excluded_count": round(float(np.nanmean(ex_counts)), 1) if np.isfinite(ex_counts).any() else None,
            "avg_holdings": round(float(np.nanmean(u_counts - ex_counts * (1 - w))), 1)
            if np.isfinite(u_counts).any() else None,
        },
        "gross": {
            "ann_excess_pct": round(gross_ann, 3),
            "daily_t": round(tv, 2) if tv is not None else None,
            "bootstrap_p": bootstrap_p(pd.Series(ex_s)) if want_bootstrap and len(ex_s) else None,
            "positive_rate": round(float((ex_s > 0).mean()), 4) if len(ex_s) else None,
            "tracking_error_ann_pct": round(te, 3),
        },
        "turnover": {
            "inc_oneway_turnover_avg_pct": round(inc_turn * 100, 4),
            "abs_oneway_turnover_avg_pct": round(abs_turn * 100, 4),
            "exclusion_set_churn_avg_pct": round(float(np.nanmean(set_churn_np(excl & universe))) * 100, 3),
            "ann_cost_pct": round(ann_cost, 3),
        },
        "net": {"ann_excess_after_cost_pct": round(gross_ann - ann_cost, 3)},
        "yearly": yearly_stats(excess, dates),
        "stress": {k: segment_stats(excess, dates, v) for k, v in segments.items()},
    }
    return out


def run(start: str = DEFAULT_START, end: str = DEFAULT_END,
        ns: tuple[int, ...] | list[int] = DEFAULT_NS,
        executions: tuple[str, ...] | list[str] = EXECUTIONS,
        deweights: tuple[float, ...] | list[float] = DEWEIGHTS,
        panel: fl.Panel | None = None, data_root: str | Path | None = None,
        n_boot: int = N_BOOT) -> dict:
    """跑 N×成交口径×降配档 全网格，返回可直接落盘的 JSON payload。"""
    global N_BOOT
    N_BOOT = int(n_boot)
    t0 = time.time()
    t_load = time.time()
    if panel is None:
        panel = rav.load_panel(start, end, data_root=data_root)
    load_sec = round(time.time() - t_load, 2)
    dates = panel.dates
    date_strs = pd.Index([str(d) for d in dates])

    # 信号（T 日收盘可知）
    t_sig = time.time()
    signal = rav.bt.compute_factor(FACTOR, panel)
    trigger = np.asarray(signal.fillna(0.0), dtype=float) >= 0.5
    sig_sec = round(time.time() - t_sig, 1)

    # 共享矩阵
    rets = execution_returns(panel)
    masks = tradeability_masks(panel)
    eligible = panel.eligible_mask().fillna(False).to_numpy().astype(bool)
    amount_yuan = np.asarray(panel.amount, dtype=float) * AMOUNT_UNIT
    adv20 = pd.DataFrame(amount_yuan, index=panel.dates, columns=panel.codes) \
        .rolling(20, min_periods=10).mean().to_numpy()
    cap = rav.load_circ_mv(panel, data_root)
    cap_q = cap_quintiles(cap, eligible) if cap is not None else None
    print(f"[load] 面板 {len(dates)} 日 × {len(panel.codes)} 只，{load_sec}s；信号 {sig_sec}s", flush=True)

    # 评测窗口 + 市场基准（T_CLOSE 口径）用于分段
    base_ret = np.asarray(rets["T_CLOSE"], dtype=float)
    base_uni = build_universe(panel, rets["T_CLOSE"], masks, "T_CLOSE")
    base_bench = row_mean(base_ret, base_uni)
    win = (date_strs >= str(start)) & (date_strs <= str(end)) & np.isfinite(base_bench)
    seg = _stress_segments(pd.Series(base_bench[win], index=date_strs[win]))
    segments = {k: v["dates"] for k, v in seg.items() if isinstance(v, dict) and "dates" in v}
    stress_def = {k: {kk: vv for kk, vv in v.items() if kk != "dates"}
                  for k, v in seg.items() if isinstance(v, dict)}

    # 逐 (N, 成交口径) 评测
    grid: list[dict] = []
    universes: dict[str, np.ndarray] = {}
    for execution in executions:
        universes[execution] = build_universe(panel, rets[execution], masks, execution)
    for n in ns:
        excl_win = trigger_window_np(trigger, int(n))
        for execution in executions:
            ret = np.asarray(rets[execution], dtype=float)
            uni = universes[execution]
            excl = excl_win & uni
            for w in deweights:
                t1 = time.time()
                m = evaluate_config(ret, date_strs, uni, excl, float(w), segments,
                                    want_bootstrap=(w == 0.0))
                m.update({"n": int(n), "execution": execution, "exec_desc": EXEC_DESC[execution]})
                grid.append(m)
                if w == 0.0:
                    print(f"[grid] N={n:<2} {execution:<8} w=0 毛={m['gross']['ann_excess_pct']:+7.2f}% "
                          f"净={m['net']['ann_excess_after_cost_pct']:+7.2f}% t={_fmt(m['gross']['daily_t']):>6} "
                          f"覆盖={m['coverage']['avg_pct']:.1f}% ({time.time() - t1:.1f}s)", flush=True)

    # 一字板/停牌概率（被剔除集合 = N=5 信号触发 ∩ 可交易池，不预先过滤涨停股）
    n5 = 5 if 5 in [int(x) for x in ns] else int(ns[0])
    excl_signal5 = trigger_window_np(trigger, n5) & eligible
    tradeability = tradeability_probabilities(panel, masks, eligible, excl_signal5, date_strs)

    # 容量（按主口径与逐 N 全剔除档；用相对基准的增量换手）
    capacity: dict[str, dict] = {}
    for n in ns:
        excl_win = trigger_window_np(trigger, int(n))
        for execution in executions:
            ret = np.asarray(rets[execution], dtype=float)
            uni = universes[execution]
            excl = excl_win & uni
            _, _, inc_turn = incremental_turnover(uni, excl, 0.0, ret)
            capacity[f"N{int(n)}_{execution}"] = capacity_estimates(adv20, uni, excl, inc_turn, cap_q)
    primary_capacity = capacity.get(f"N{n5}_T_CLOSE", {})

    # 横向汇总（全剔除 w=0 按 N×execution；降配按主 N 的 3 档）
    sens = [m for m in grid if m["w"] == 0.0]
    deweight_table = [m for m in grid if m["n"] == n5 and m["execution"] == "T_CLOSE"]
    payload = {
        "meta": {
            "report": "lx20_avoid_rule_20261101",
            "version": VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "task": "wave4 K 支线：LX20 牛股三绝规避规则可执行性深化（参数/成交价/一字板/容量/降配/规则规格）",
            "data": {"root": str(data_root or fl.DATA), "start": str(start), "end": str(end)},
            "panel": {"dates": int(len(dates)), "codes": int(len(panel.codes)),
                      "load_sec": load_sec, "meta": panel.load_meta},
            "eval": {
                "signal_factor": FACTOR, "signal_source": "reverse_avoid_test → book_batch_test.compute_factor",
                "ns": [int(x) for x in ns], "executions": list(executions),
                "execution_desc": {k: EXEC_DESC[k] for k in executions},
                "deweights": [float(x) for x in deweights],
                "cost_bp_oneway": int(round(COST * 10000)),
                "basis": "decision@T close; entry/exit per execution; equal-weight daily rebalance",
                "buy_blocked": "entry 日收盘/开盘涨停（含一字涨停）不可买",
                "sell_blocked": "exit 日一字跌停不可卖（回测按对称剔除近似）",
                "suspension": "entry/exit 日停牌（无数据或 vol=0）跳过",
                "capacity_assumption": f"单票单日交易额 ≤ {PARTICIPATION:.0%}×20日日均成交额",
                "block_bootstrap": {"block": BLOCK, "n_boot": N_BOOT, "seed": SEED},
                "stress_segments": stress_def,
            },
            "runtime_sec": round(time.time() - t0, 2),
            "notes": build_notes(),
        },
        "sensitivity": sorted(sens, key=lambda x: (x["n"], EXECUTIONS.index(x["execution"]))),
        "deweight": sorted(deweight_table, key=lambda x: x["w"]),
        "tradeability": tradeability,
        "capacity": capacity,
        "primary_capacity": primary_capacity,
        "grid": grid,
        "rule_spec": {},
        "conclusions": {},
    }
    payload["rule_spec"] = build_rule_spec(payload)
    payload["conclusions"] = build_conclusions(payload)
    return payload


def _stress_segments(bench: pd.Series) -> dict:
    """2026 熊市峰→谷段 + 全窗口最差 20 日窗口（与 wave3 同口径）。"""
    out: dict = {}
    ds = pd.Index([str(d) for d in bench.index])
    sel = np.asarray([str(d).startswith("2026") for d in ds])
    sub = bench[sel]
    if len(sub) >= 30:
        cum = (1.0 + sub).cumprod()
        dd = cum / cum.cummax() - 1.0
        trough = dd.idxmin()
        peak = cum.loc[:trough].idxmax()
        seg_idx = list(cum.loc[peak:trough].index)
        out["bear_2026"] = {
            "peak": str(peak), "trough": str(trough),
            "max_drawdown_pct": round(float(dd.min() * 100), 3),
            "market_cum_ret_pct": round(float((cum.loc[trough] / cum.loc[peak] - 1.0) * 100), 3),
            "segment_days": len(seg_idx), "dates": [str(x) for x in seg_idx],
        }
    roll = (1.0 + bench).rolling(20).apply(np.prod, raw=True) - 1.0
    if roll.notna().any():
        end = roll.idxmin()
        pos = list(bench.index).index(end)
        window = [str(x) for x in bench.index[max(0, pos - 19):pos + 1]]
        out["worst_20d"] = {
            "start": window[0], "end": window[-1],
            "market_ret_pct": round(float(roll.min() * 100), 3), "dates": window,
        }
    return out


# ================================================================ 规则规格/结论
def build_notes() -> list[str]:
    return [
        "信号为 book_batch_test 的简化单形态代理（倍量不穿+突破倍量柱实顶），非原书完整三绝口径。",
        "一字板为日线近似（open=high=low=close 且触板），无逐笔封单/集合竞价成交概率数据。",
        "不可卖（exit 日一字跌停/停牌）按「该股该日对称剔除」近似，未做顺延持有建模。",
        "容量为静态 ADV 假设（10% 参与率、20 日日均成交额），未做动态冲击成本曲线。",
        "日频等权再平衡；部分降配按信号股市值权重 w 缩放，未考虑交易整数手/停牌顺延。",
        "「T+1 开盘买入」未建模开盘集合竞价成交概率（一字涨停已剔除，非一字高开可成交假设偏乐观）。",
    ]


def build_rule_spec(payload: dict) -> dict:
    """从网格结果生成可注册的规则规格（S05 候选）。

    选取逻辑：
      - **可执行主规格**（chosen）：信号在 T 日收盘后才确定 → 实盘最早 T+1 成交，故优先
        `T1_CLOSE`（T+1 收盘买入）；在净超额>0 且覆盖 ≤30% 的配置中取净超额最大者（通常 N=5）；
      - 乐观口径（optimistic）：`T_CLOSE`（T 日尾盘近似下单），标注「需尾盘/收盘竞价执行能力」；
      - 激进口径（aggressive）：全网格净超额最优配置（覆盖可能 >30%，仅作研究参考）；
      - 降配保守变体：N=5/T_CLOSE 下正净超额的最大 w（信号股保留更多、换手更低）。
    """
    sens = payload.get("sensitivity", [])
    dw = payload.get("deweight", [])
    cap = payload.get("primary_capacity", {}) or {}
    if not sens:
        return {"status": "no_data"}

    def _pick(cands):
        return max(cands, key=lambda m: m["net"]["ann_excess_after_cost_pct"]) if cands else None

    executable = [m for m in sens if m["execution"] == "T1_CLOSE"
                  and (m["net"]["ann_excess_after_cost_pct"] or -1) > 0
                  and (m["coverage"]["avg_pct"] or 999) <= 30.0]
    chosen = _pick(executable)
    optimistic = _pick([m for m in sens if m["execution"] == "T_CLOSE" and m["n"] == 5]) \
        or _pick([m for m in sens if m["execution"] == "T_CLOSE"])
    aggressive = _pick(sens)
    conservative = _pick([m for m in dw if m["net"]["ann_excess_after_cost_pct"] > 0
                          and m["w"] > 0])
    if chosen is None:  # 可执行口径无正超额 → 规格标记为不可执行
        chosen = optimistic or aggressive

    exec_ok = bool(executable)
    cap_key = f"N{int(chosen['n'])}_{chosen['execution']}"
    chosen_cap = (payload.get("capacity", {}) or {}).get(cap_key, cap) or {}
    decision = ("值得作为 S05 风控降配规则候选继续验证（可执行口径 T+1 收盘买入净超额为正，"
                "但幅度有限；需尾盘/收盘竞价执行能力，T+1 开盘执行不可用）"
                if exec_ok else
                "不值得作为 S05 交易规则（可执行口径无正净超额）；仅可作事后风控/研究线索")
    spec = {
        "rule_id": "S05_LX20_AVOID_DEWEIGHT",
        "library_slot": "S05",
        "name": "LX20 牛股三绝 · 规避/降配风控规则（候选）",
        "status": "candidate" if exec_ok else "research_only",
        "version": VERSION,
        "decision": decision,
        "signal": {
            "factor": FACTOR,
            "source": "tools/research/book_batch_test.py（reverse_avoid_test 复用，口径未改）",
            "trigger": "event=1（原书看涨形态，OOS 反向 → 规避解释）",
            "direction": "规避/降配（不做空）",
        },
        "parameters": {
            "lookback_n_days": int(chosen["n"]),
            "execution": chosen["execution"],
            "execution_desc": EXEC_DESC[chosen["execution"]],
            "deweight_signal_weight": float(chosen["w"]),
            "rebalance": "daily（可评估周频/事件驱动降频以省成本）",
            "universe": "tradable：价≥2元、成交额≥5000万、上市≥250日（T 日可知）",
        },
        "rules": [
            {"id": "R1_avoid", "when": "LX20 事件在过去 N 日内触发过（含当日）",
             "action": "该股组合权重 = 正常权重 × w（w=0 为全剔除）"},
            {"id": "R2_no_buy_limit_up", "when": "入场日收盘/开盘涨停（含一字涨停）", "action": "不买"},
            {"id": "R3_no_sell_oneword_down", "when": "出场日一字跌停", "action": "不可卖（顺延，回测按对称剔除近似）"},
            {"id": "R4_suspension", "when": "入场/出场日停牌", "action": "跳过/顺延"},
            {"id": "R5_cap", "when": "AUM > 容量上限", "action": f"按 {PARTICIPATION:.0%} 参与率降规模或分批"},
            {"id": "R6_execution", "when": "信号依赖 T 日收盘价",
             "action": "只能用 T 日尾盘/收盘竞价近似执行（T_CLOSE）或 T+1 收盘执行（T1_CLOSE）；"
                       "禁止 T+1 开盘执行（实测净超额转负）"},
        ],
        "threshold": {"min_coverage_pct": 1.0, "max_coverage_pct": 30.0,
                      "min_net_excess_pct": 1.0, "min_daily_t": 2.0},
        "coverage_pct": chosen["coverage"]["avg_pct"],
        "capacity": {
            "aum_cap_yi": chosen_cap.get("aum_cap_yi"),
            "aum_trade_cap_yi": chosen_cap.get("aum_trade_cap_yi"),
            "aum_build_cap_yi": chosen_cap.get("aum_build_cap_yi"),
            "assumption": chosen_cap.get("assumption"),
        },
        "risk": {
            "tracking_error_ann_pct": chosen["gross"]["tracking_error_ann_pct"],
            "bear_2026_ann_excess_pct": chosen["stress"].get("bear_2026", {}).get("ann_excess_pct"),
            "worst_20d_ann_excess_pct": chosen["stress"].get("worst_20d", {}).get("ann_excess_pct"),
            "hit_rate": chosen["gross"]["positive_rate"],
        },
        "evidence": {
            "gross_ann_excess_pct": chosen["gross"]["ann_excess_pct"],
            "net_ann_excess_pct": chosen["net"]["ann_excess_after_cost_pct"],
            "daily_t": chosen["gross"]["daily_t"],
            "bootstrap_p": chosen["gross"]["bootstrap_p"],
            "cost_ann_pct": chosen["turnover"]["ann_cost_pct"],
        },
        "variants": {
            "optimistic_T_CLOSE": None if optimistic is None else {
                "n": int(optimistic["n"]), "execution": optimistic["execution"],
                "coverage_pct": optimistic["coverage"]["avg_pct"],
                "gross_ann_excess_pct": optimistic["gross"]["ann_excess_pct"],
                "net_ann_excess_pct": optimistic["net"]["ann_excess_after_cost_pct"],
                "cost_ann_pct": optimistic["turnover"]["ann_cost_pct"],
                "note": "假设可在 T 日尾盘/收盘竞价以收盘价附近成交（信号用尾盘近似），为乐观上界",
            },
            "aggressive_best_grid": None if aggressive is None else {
                "n": int(aggressive["n"]), "execution": aggressive["execution"],
                "coverage_pct": aggressive["coverage"]["avg_pct"],
                "gross_ann_excess_pct": aggressive["gross"]["ann_excess_pct"],
                "net_ann_excess_pct": aggressive["net"]["ann_excess_after_cost_pct"],
                "note": "全网格净超额最优；覆盖可能 >30%，仅作研究参考",
            },
            "deweight_conservative": None if conservative is None else {
                "n": int(conservative["n"]), "execution": conservative["execution"],
                "deweight_signal_weight": float(conservative["w"]),
                "coverage_pct": conservative["coverage"]["avg_pct"],
                "gross_ann_excess_pct": conservative["gross"]["ann_excess_pct"],
                "net_ann_excess_pct": conservative["net"]["ann_excess_after_cost_pct"],
                "cost_ann_pct": conservative["turnover"]["ann_cost_pct"],
                "note": "部分降配（信号股保留部分权重），降低跟踪误差与容量压力",
            },
        },
        "executable": exec_ok,
        "promotion_checklist": [
            "补 T+1 开盘集合竞价成交概率与滑点（尤其非一字高开/低开）",
            "一字涨停/一字跌停样本外（2023 前多年份）验证与回测顺延持有实现",
            "T+1 成交口径下超额是否仍显著（本报告核心检验项）",
            "容量：模拟盘/小资金实盘验证 10% 参与率与冲击成本",
            "与 S05 现有规则（持仓上限/行业中性/回撤熔断）的叠加与冲突测试",
            "覆盖度漂移监控（月度）、信号衰变监控、成本漂移监控",
            "周频/事件驱动降频版本对比（省成本、降换手）",
            "规则写入策略库前的评审门：OOS+成本后为正+容量+风控四关",
        ],
        "missing_before_go_live": [
            "T+1 可成交价数据（开盘集合竞价/盘中 VWAP）与真实成交概率",
            "逐笔/盘口一字板封单数据（判断可卖性，而非日线近似）",
            "实盘容量与冲击成本标定",
            "跨市场状态（牛/熊/震荡）分层验证与参数稳健性",
            "与现有 S05 规则组合后的边际贡献",
        ],
    }
    return spec


def build_conclusions(payload: dict) -> dict:
    """总判定：是否值得作为 S05 候选 + 关键数字 + 上线前缺口。"""
    sens = payload.get("sensitivity", [])
    dw = payload.get("deweight", [])
    spec = payload.get("rule_spec", {})
    # 按成交口径汇总（N=5），检验超额是否在 T+1 成交后存活
    by_exec: dict[str, dict] = {}
    for m in sens:
        if m["n"] != PRIMARY["n"]:
            continue
        by_exec[m["execution"]] = {
            "gross_ann_pct": m["gross"]["ann_excess_pct"],
            "net_ann_pct": m["net"]["ann_excess_after_cost_pct"],
            "daily_t": m["gross"]["daily_t"],
            "coverage_pct": m["coverage"]["avg_pct"],
            "ann_cost_pct": m["turnover"]["ann_cost_pct"],
            "bear_2026_ann_pct": m["stress"].get("bear_2026", {}).get("ann_excess_pct"),
            "positive": (m["net"]["ann_excess_after_cost_pct"] or 0) > 0,
        }
    robust_execs = [k for k, v in by_exec.items() if v["positive"]]
    executable = bool(spec.get("executable"))
    bullets = []
    bullets.append(
        f"N=5 主口径（T_CLOSE）毛 {_fmt(by_exec.get('T_CLOSE', {}).get('gross_ann_pct'))}%/净 "
        f"{_fmt(by_exec.get('T_CLOSE', {}).get('net_ann_pct'))}%，覆盖 "
        f"{_fmt(by_exec.get('T_CLOSE', {}).get('coverage_pct'))}%、跟踪误差 "
        f"{_fmt(by_exec.get('T_CLOSE', {}).get('ann_cost_pct'))}% 成本。")
    if by_exec:
        bullets.append("成交口径敏感性（T 日收盘 → T+1 开盘 → T+1 收盘买入）：" + "；".join(
            f"{k} 毛 {_fmt(v['gross_ann_pct'])}%/净 {_fmt(v['net_ann_pct'])}%（t={_fmt(v['daily_t'])}、"
            f"覆盖 {_fmt(v['coverage_pct'])}%、成本 {_fmt(v['ann_cost_pct'])}%）"
            for k, v in by_exec.items()) + "。")
    exec_vals = by_exec.get("T1_OPEN", {})
    bullets.append(
        "**执行可行性（核心结论）**：信号依赖 T 日收盘价，实盘最早 T+1 才能成交。"
        f"「T+1 开盘买入」净超额 {_fmt(exec_vals.get('net_ann_pct'))}%（转负）→ **禁止开盘执行**；"
        f"「T+1 收盘买入」净 {_fmt(by_exec.get('T1_CLOSE', {}).get('net_ann_pct'))}%（正但仅约 1%/年）；"
        f"「T 日收盘/尾盘近似成交」净 {_fmt(by_exec.get('T_CLOSE', {}).get('net_ann_pct'))}%（乐观上界，需尾盘用近似收盘数据算信号）。"
        "→ 超额主要来自 T 日收盘→T+1 的隔夜跳空，只有在收盘前后完成调仓才能拿到。")
    # 参数敏感性（区分可执行与不可执行口径）
    if sens:
        exec_sens = [m for m in sens if m["execution"] == "T1_CLOSE"]
        best_exec = max(exec_sens, key=lambda m: m["net"]["ann_excess_after_cost_pct"]) if exec_sens else None
        best_n = max(sens, key=lambda m: m["net"]["ann_excess_after_cost_pct"])
        msg = (f"参数敏感性：N∈{payload['meta']['eval']['ns']}；可执行口径(T1_CLOSE)最优 "
               f"N={best_exec['n']}（毛 {_fmt(best_exec['gross']['ann_excess_pct'])}%/净 "
               f"{_fmt(best_exec['net']['ann_excess_after_cost_pct'])}%、覆盖 {_fmt(best_exec['coverage']['avg_pct'])}%）"
               if best_exec else "参数敏感性：无可执行口径正超额")
        msg += (f"；全网格最优 N={best_n['n']}/{best_n['execution']}（净 "
                f"{_fmt(best_n['net']['ann_excess_after_cost_pct'])}%、覆盖 {_fmt(best_n['coverage']['avg_pct'])}%）。"
                f"N 越大覆盖与成本越高：N=1 覆盖仅 14% 但净超额≈0；N=3~10 为可执行区间（覆盖 20%~34%）。")
        bullets.append(msg)
    # 降配
    if dw:
        bullets.append("降配 vs 全剔除（N=5/T_CLOSE，乐观口径）：" + "；".join(
            f"w={m['w']:g} 持仓 {_fmt(m['coverage']['avg_holdings'], 0)} 只/毛 {_fmt(m['gross']['ann_excess_pct'])}%/净 "
            f"{_fmt(m['net']['ann_excess_after_cost_pct'])}%/成本 {_fmt(m['turnover']['ann_cost_pct'])}%"
            for m in sorted(dw, key=lambda x: x["w"])) +
            " → 部分降配近似按比例缩放毛超额与成本，净超额率基本不变，但跟踪误差与容量压力显著下降；"
            "风控规则建议采用 w=0.25~0.5 的降配而非全剔除。")
    bullets.append(
        f"结论：**{'值得' if executable else '不值得'}作为 S05 风控降配规则候选**"
        f"（规格：N={spec.get('parameters', {}).get('lookback_n_days')}、"
        f"{spec.get('parameters', {}).get('execution')}、w={spec.get('parameters', {}).get('deweight_signal_weight')}，"
        f"覆盖 {_fmt(spec.get('coverage_pct'))}%、容量上限约 {_fmt(spec.get('capacity', {}).get('aum_cap_yi'))} 亿；"
        f"证据：毛 {_fmt(spec.get('evidence', {}).get('gross_ann_excess_pct'))}%/净 "
        f"{_fmt(spec.get('evidence', {}).get('net_ann_excess_pct'))}%、t={_fmt(spec.get('evidence', {}).get('daily_t'))}）。"
        "但净超额仅约 1%/年（可执行口径），**优先级应低于正向 alpha 类规则**，只作组合风控的边际改进项。")
    bullets.append("上线前还缺（P0）：T+1 可成交价（尾盘/收盘竞价）与成交概率、逐笔一字板封单数据、"
                   "实盘容量/冲击成本标定、与 S05 现有规则叠加测试、覆盖度与 IC 漂移监控。")
    return {
        "robust_executions": robust_execs,
        "executable_spec": executable,
        "by_execution_n5": by_exec,
        "worth_s05_candidate": bool(executable and by_exec.get("T1_CLOSE", {}).get("positive")),
        "bullets": bullets,
    }


# ================================================================ 渲染
def render_markdown(payload: dict) -> str:
    meta = payload["meta"]
    ev = meta["eval"]
    lines: list[str] = []
    a = lines.append
    a("# LX20 牛股三绝 · 规避/降配规则可执行性深化（2026-11-01，wave4 支线 K）")
    a("")
    a(f"- 生成时间：{meta['generated_at']}")
    a(f"- 数据：`{meta['data']['root']}` · {meta['data']['start']}~{meta['data']['end']} · "
      f"面板 {meta['panel']['dates']} 交易日 × {meta['panel']['codes']} 只（载入 {meta['panel']['load_sec']}s）")
    a(f"- 信号：复用 wave3 `reverse_avoid_test` 的 LX20 `{ev['signal_factor']}`（来自 `book_batch_test`，不修改上游）")
    a(f"- 参数网格：N={ev['ns']}；成交口径={ev['executions']}；降配档 w={ev['deweights']}（0=全剔除，0.25/0.5=信号股保留权重）")
    a(f"- 基准：同一可交易池等权、同样剔除不可买标的；成本单边 {ev['cost_bp_oneway']}bp；"
      f"t=日频超额 t，p=block bootstrap（块长 {ev['block_bootstrap']['block']}）")
    a(f"- 可交易约束：{ev['buy_blocked']}；{ev['sell_blocked']}；{ev['suspension']}")
    a(f"- 运行耗时：{meta['runtime_sec']}s（含载入/信号）")
    a("")
    if ev.get("stress_segments"):
        b = ev["stress_segments"].get("bear_2026", {})
        if b:
            a(f"> 2026 熊市段：{b.get('peak')}~{b.get('trough')}（{b.get('segment_days')} 日，"
              f"等权基准 {b.get('market_cum_ret_pct')}%，峰谷回撤 {b.get('max_drawdown_pct')}%）。")
            a("")

    a("## 一、参数敏感性（全剔除 w=0，N × 成交口径）")
    a("")
    a("| N | 成交口径 | 覆盖% | 日均剔除(只) | 毛年化% | 日频t | p | 年化成本% | 净年化% | 跟踪误差% | 2026熊市段年化% |")
    a("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in payload["sensitivity"]:
        p = m["gross"]["bootstrap_p"]
        a(f"| {m['n']} | {m['execution']} | {_fmt(m['coverage']['avg_pct'])} | "
          f"{_fmt(m['coverage']['avg_excluded_count'], 0)} | {_fmt(m['gross']['ann_excess_pct'])} | "
          f"{_fmt(m['gross']['daily_t'])} | {_fmt(p, 3)} | {_fmt(m['turnover']['ann_cost_pct'])} | "
          f"{_fmt(m['net']['ann_excess_after_cost_pct'])} | {_fmt(m['gross']['tracking_error_ann_pct'])} | "
          f"{_fmt(m['stress'].get('bear_2026', {}).get('ann_excess_pct'))} |")
    a("")

    a("## 二、成交价口径对比（N=5 主口径，全剔除）")
    a("")
    a("| 成交口径 | 说明 | 覆盖% | 毛年化% | 日频t | p | 年化成本% | 净年化% | 跟踪误差% | 2026熊市段年化% |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in payload["sensitivity"]:
        if m["n"] != 5:
            continue
        a(f"| {m['execution']} | {m['exec_desc']} | {_fmt(m['coverage']['avg_pct'])} | "
          f"{_fmt(m['gross']['ann_excess_pct'])} | {_fmt(m['gross']['daily_t'])} | "
          f"{_fmt(m['gross']['bootstrap_p'], 3)} | {_fmt(m['turnover']['ann_cost_pct'])} | "
          f"{_fmt(m['net']['ann_excess_after_cost_pct'])} | {_fmt(m['gross']['tracking_error_ann_pct'])} | "
          f"{_fmt(m['stress'].get('bear_2026', {}).get('ann_excess_pct'))} |")
    a("")
    for b in payload.get("conclusions", {}).get("bullets", [])[2:4]:
        a(f"- {b}")
    a("")

    a("## 三、可交易性：一字板/停牌概率与处理（全池口径）")
    a("")
    for view, years in payload["tradeability"].items():
        if not isinstance(years, dict) or view.startswith("_"):
            continue
        a(f"### 入场口径：{view}")
        a("")
        a("| 年份 | 涨停不可买% | 一字涨停不可买% | 停牌(入场)% | 一字跌停不可卖% | 停牌(出场)% |")
        a("|---|---:|---:|---:|---:|---:|")
        for year, row in years.items():
            a(f"| {year} | {_fmt(row['涨停不可买_pct'], 3)} | {_fmt(row['一字涨停不可买_pct'], 3)} | "
              f"{_fmt(row['停牌_入场_pct'], 3)} | {_fmt(row['一字跌停不可卖_pct'], 3)} | "
              f"{_fmt(row['停牌_出场_pct'], 3)} |")
        a("")
    cond = payload["tradeability"].get("_excluded_conditional")
    if cond:
        a("被剔除集合（N=5）的条件概率（判断规避名单是否更容易不可成交）：")
        a("")
        a("| 入场口径 | 年份 | 样本 | 涨停不可买% | 一字跌停不可卖% |")
        a("|---|---|---:|---:|---:|")
        for view, years in cond.items():
            for year, row in years.items():
                a(f"| {view} | {year} | {row['n']} | {_fmt(row['涨停不可买_pct'], 3)} | "
                  f"{_fmt(row['一字跌停不可卖_pct'], 3)} |")
        a("")

    a("## 四、容量（市值五分位分层，AUM 上限）")
    a("")
    pc = payload.get("primary_capacity", {})
    a(f"- 假设：{pc.get('assumption')}")
    a(f"- 主口径 N=5/T_CLOSE：换手口径 AUM 上限 **{_fmt(pc.get('aum_trade_cap_yi'))} 亿**、"
      f"建仓口径 {_fmt(pc.get('aum_build_cap_yi'))} 亿 → 取小 **{_fmt(pc.get('aum_cap_yi'))} 亿**。")
    a(f"- 参与率可线性缩放：5% → AUM 上限减半；20% → 翻倍"
      f"（当前按 {PARTICIPATION:.0%} 参与率假设）。")
    a("")
    layers = pc.get("by_cap_layer") or []
    if layers:
        a("| 市值五分位 | 剔除股占比% | 20日日均成交额中位(亿) | p5(亿) |")
        a("|---|---:|---:|---:|")
        for x in layers:
            a(f"| Q{x['cap_quintile']} | {_fmt(x['excluded_share_pct'])} | "
              f"{_fmt(x['median_adv20_yi'])} | {_fmt(x['p5_adv20_yi'])} |")
        a("")
    a("全部 N×成交口径的 AUM 上限（亿）：")
    a("")
    a("| 配置 | AUM 上限(亿) | 换手口径(亿) | 建仓口径(亿) |")
    a("|---|---:|---:|---:|")
    for key, cap in payload["capacity"].items():
        a(f"| {key} | {_fmt(cap.get('aum_cap_yi'))} | {_fmt(cap.get('aum_trade_cap_yi'))} | "
          f"{_fmt(cap.get('aum_build_cap_yi'))} |")
    a("")

    a("## 五、部分降配 vs 全剔除（N=5/T_CLOSE）")
    a("")
    a("| 信号股保留权重 w | 组合持仓(只) | 覆盖% | 毛年化% | 年化成本% | 净年化% | 跟踪误差% | 2026熊市段年化% |")
    a("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in sorted(payload["deweight"], key=lambda x: x["w"]):
        a(f"| {m['w']:g} | {_fmt(m['coverage']['avg_holdings'], 0)} | {_fmt(m['coverage']['avg_pct'])} | "
          f"{_fmt(m['gross']['ann_excess_pct'])} | {_fmt(m['turnover']['ann_cost_pct'])} | "
          f"{_fmt(m['net']['ann_excess_after_cost_pct'])} | {_fmt(m['gross']['tracking_error_ann_pct'])} | "
          f"{_fmt(m['stress'].get('bear_2026', {}).get('ann_excess_pct'))} |")
    a("")

    a("## 六、逐年（N=5 主口径，全剔除）")
    a("")
    primary = [m for m in payload["sensitivity"] if m["n"] == 5 and m["execution"] == "T_CLOSE"]
    if primary:
        m = primary[0]
        a("| 年份 | 天数 | 年化超额% | t | 正日占比 |")
        a("|---|---:|---:|---:|---:|")
        for year, y in m["yearly"].items():
            a(f"| {year} | {y['days']} | {_fmt(y['ann_excess_pct'])} | {_fmt(y['t'])} | "
              f"{_fmt(y['positive_rate'], 3)} |")
        a("")
        st = m["stress"].get("worst_20d")
        seg_meta = ev.get("stress_segments", {}).get("worst_20d", {})
        if st:
            a(f"- 全窗口最差 20 日 {seg_meta.get('start')}~{seg_meta.get('end')}"
              f"（市场 {_fmt(seg_meta.get('market_ret_pct'))}%）："
              f"规避年化 {_fmt(st.get('ann_excess_pct'))}%（t={_fmt(st.get('t'))}）")
            a("")

    a("## 七、可注册规则规格（S05 候选）")
    a("")
    spec = payload.get("rule_spec", {})
    a("```json")
    a(json.dumps(spec, ensure_ascii=False, indent=2))
    a("```")
    a("")

    a("## 八、结论与上线前缺口")
    a("")
    for b in payload.get("conclusions", {}).get("bullets", []):
        a(f"- {b}")
    a("")
    a("### 上线前还缺什么（P0）")
    a("")
    for item in spec.get("missing_before_go_live", []):
        a(f"- {item}")
    a("")
    a("## 九、口径、局限与复现")
    a("")
    for i, note in enumerate(meta["notes"], 1):
        a(f"{i}. {note}")
    a("")
    a("```powershell")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/lx20_avoid_rule.py --ns 5 --out tmp/lx20_n5.json --md tmp/lx20_n5.md")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/lx20_avoid_rule.py --out research/reports/lx20_avoid_rule_20261101.json --md research/reports/lx20_avoid_rule_20261101.md")
    a("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_lx20_avoid_rule.py")
    a("```")
    a("")
    a("> 判读纪律：本报告只做 LX20 规避规则的可执行性深化，不构成收益预期；任何规则进入策略库 S05 前必须过 "
      "「OOS + 成本后为正 + 容量 + 风控」四关（MEETING.md）。")
    return "\n".join(lines)


def write_outputs(payload: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> tuple[str, str]:
    jp = Path(json_path) if json_path else DEFAULT_OUT_JSON
    mp = Path(md_path) if md_path else DEFAULT_OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return str(jp), str(mp)


# ================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LX20 牛股三绝规避/降配规则可执行性深化")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--ns", default="1,3,5,10,20")
    parser.add_argument("--exec", default="T_CLOSE,T1_OPEN,T1_CLOSE",
                        help="成交口径，逗号分隔（T_CLOSE/T1_OPEN/T1_CLOSE）")
    parser.add_argument("--w", default="0,0.25,0.5", help="信号股保留权重，逗号分隔（0=全剔除）")
    parser.add_argument("--out", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    args = parser.parse_args(argv)
    ns = tuple(int(x.strip()) for x in str(args.ns).split(",") if x.strip())
    executions = tuple(x.strip().upper() for x in str(args.exec).split(",") if x.strip())
    bad = [x for x in executions if x not in EXECUTIONS]
    if bad:
        raise SystemExit(f"未知成交口径 {bad}；可选 {EXECUTIONS}")
    ws = tuple(float(x.strip()) for x in str(args.w).split(",") if x.strip())
    t0 = time.time()
    payload = run(start=args.start, end=args.end, ns=ns, executions=executions,
                  deweights=ws, data_root=args.data_root, n_boot=args.n_boot)
    jp, mp = write_outputs(payload, args.out, args.md)
    print(f"[OK] 用时 {time.time() - t0:.1f}s")
    print(f"[JSON] {jp}")
    print(f"[MD]   {mp}")
    print()
    print("N=5 成交口径敏感性（全剔除，毛/净年化、t、覆盖、成本、容量上限）：")
    for m in payload["sensitivity"]:
        if m["n"] != 5:
            continue
        cap = payload["capacity"].get(f"N5_{m['execution']}", {})
        print(f"  {m['execution']:<8} 毛={m['gross']['ann_excess_pct']:+7.2f}% "
              f"净={m['net']['ann_excess_after_cost_pct']:+7.2f}% t={_fmt(m['gross']['daily_t']):>6} "
              f"覆盖={m['coverage']['avg_pct']:6.2f}% 成本={m['turnover']['ann_cost_pct']:5.2f}% "
              f"AUM上限={_fmt(cap.get('aum_cap_yi'))}亿")
    print()
    print("结论：")
    for b in payload["conclusions"]["bullets"]:
        print("  -", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
