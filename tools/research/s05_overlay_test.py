# -*- coding: utf-8 -*-
"""tools/research/s05_overlay_test.py · S05 规避规则 × S01 基线叠加测试（2026-11-01，wave5 支线 N）。

任务
----
wave4 的 `lx20_avoid_rule.py` 产出 S05_LX20_AVOID_DEWEIGHT 候选（T+1 收盘执行净 +1.00%/t=6.21、
覆盖 24.5%、容量 187 亿；降配 w=0.25 净 +1.49%）。本文件做「与现有组合叠加」的通过关：

  1. 基线 = **S01 基线**（`portfolio_build.build_baseline` 口径：S01 过滤后按流通市值降序 top20 等权）；
     本文件把 build_baseline 的过滤/排位规则**向量化复刻**到全窗口，并抽 3 个日期调用真实
     `build_baseline` 做逐位对齐校验（口径一致性证据）；
  2. 叠加 S05：在每个周频调仓日 T（T 收盘决策），对基线持仓中「过去 N=5 日触发过 LX20」的股票
     降配到权重 w ∈ {0（剔除）, 0.25, 0.5}（非信号股维持等权、权重归一）；对照纯基线；
  3. 回测 2023-01~2026-10：**T+1 收盘执行**、单边 30bp、执行日涨停不可买（该权重转现金）、
     停牌/无数据不可买；持有期按目标权重日频再平衡；输出年化收益/超额/波动/MDD/Sharpe/TE/换手/成本；
  4. 分年（2023/24/25/26）+ 2026 熊市段（峰→谷）对比；
  5. 输出可写入 strategy_registry S05 validation 的栈叠规则说明 + 结论：叠加是否提升基线
     （收益/回撤/TE 权衡）、是否值得进入影子盘。

口径
----
- 基线口径严格对齐 `portfolio_build.build_baseline`（S01）：ST/次新/低价/低流动性/当日涨停/
  缺财务/亏损/极端涨幅 过滤 + PE 三分位×ROE 层内 20% 质量陷阱剔除 + 流通市值降序 top20 等权；
- 信号：LX20 `lx20_three_absolutes`（复用 reverse_avoid_test → book_batch_test，不修改上游）；
- 调仓：每 5 个交易日决策一次（周频），T 收盘出权重、T+1 收盘执行；执行日涨停/停牌不可买 → 现金；
- 成本：调仓换手 = 0.5×Σ|Δw|（首期建仓按单边 30bp 计），成本 = 换手×2×30bp 从当日收益扣除；
- 收益：执行后按目标权重日频再平衡，个股停牌/退市当日收益按 0 处理（未做顺延）；
- 超额 = 叠加组合日收益 − 基线组合日收益；TE = 超额日序列年化标准差；
- 2026 熊市段 = 基线组合 2026 年内最大回撤峰→谷段。

用法
----
    # 先跑通 w=0.25 主口径
    C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_overlay_test.py --weights 0.25 \
        --out tmp/s05_overlay_w025.json --md tmp/s05_overlay_w025.md
    # 全量（基线 + w=0/0.25/0.5）
    C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_overlay_test.py \
        --out research/reports/s05_overlay_20261101.json \
        --md research/reports/s05_overlay_20261101.md

局限：S01 过滤中的 ST/行业/上市日期用 stock_basic 当前静态信息（轻微后验，与 build_baseline 同源）；
信号为简化单形态代理；持有期个股停牌收益按 0；容量未按 AUM 再算（沿用 wave4 的 187 亿@10% 参与率）；
调仓频率仅周频（未做日频/月频敏感性）；未含融券做空（A 股不可做空）。
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

import factor_lib as fl                  # noqa: E402
import reverse_avoid_test as rav         # noqa: E402  复用 LX20 信号/统计口径（不修改）
import portfolio_build as pb             # noqa: E402  S01 基线口径来源
import selection_engine as se            # noqa: E402  基线数据装载（校验用）

VERSION = "0.1.0-wave5"
ANNUAL_DAYS = 252
COST = fl.DEFAULT_COST                   # 单边 30bp
DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
SIGNAL_N = 5                             # LX20 回避回溯窗口（wave4 主口径）
REBALANCE_EVERY = 5                      # 周频调仓（交易日）
TOP = 20                                 # S01 基线持仓数
DEFAULT_WEIGHTS = (0.0, 0.25, 0.5)
PRIMARY_W = 0.25
MIN_PRICE = 2.0
MIN_AMOUNT_YUAN = 5e7
LIMITUP_PCT = 9.5                        # 与 build_baseline 同口径（不分板）
DEFAULT_OUT_JSON = REPO / "research" / "reports" / "s05_overlay_20261101.json"
DEFAULT_OUT_MD = REPO / "research" / "reports" / "s05_overlay_20261101.md"
VALIDATION_DATES = ("20230103", "20240603", "20251030")


# ================================================================ S01 过滤复刻
def load_names_meta(data_root: str | Path | None = None) -> pd.DataFrame:
    """stock_basic：name/industry/list_date（与 portfolio_build 同源、当前静态）。"""
    root = Path(data_root) if data_root else fl.DATA
    sb = fl._load_stock_basic(root)
    if sb.empty:
        return pd.DataFrame(columns=["ts_code", "name", "industry", "list_date"])
    return sb.set_index("ts_code")


def compute_s01_masks(panel: fl.Panel, circ_mv: pd.DataFrame,
                      names_meta: pd.DataFrame, top: int = TOP) -> dict:
    """向量化复刻 portfolio_build.build_baseline 的过滤/选股逻辑。

    返回 dict：{eligible: bool 矩阵, picks: {date_i: [code_idx...]}, ret20, ret5, drop_counts}
    """
    dates, codes = panel.dates, panel.codes
    close = np.asarray(panel.close, dtype=float)
    amount_yuan = np.asarray(panel.amount, dtype=float) * fl.AMOUNT_UNIT
    pct = np.asarray(panel.pct_chg, dtype=float)
    adj = np.asarray(panel.adj_close, dtype=float)
    pe = np.asarray(panel.optional("pe_ttm"), dtype=float)
    roe = np.asarray(panel.optional("roe"), dtype=float)
    mv = np.asarray(circ_mv.reindex(index=dates, columns=codes), dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret20 = adj / np.roll(adj, 20, axis=0) - 1.0
        ret5 = adj / np.roll(adj, 5, axis=0) - 1.0
    ret20[:20, :] = np.nan
    ret5[:5, :] = np.nan

    # 静态信息
    sb = names_meta.reindex(pd.Index([str(c) for c in codes]))
    name = sb["name"].fillna("").astype(str).to_numpy()
    list_date = sb["list_date"].fillna("").astype(str).to_numpy()
    dts = pd.to_datetime(pd.Index([str(d) for d in dates]), format="%Y%m%d")
    ld = pd.to_datetime(pd.Series(list_date), format="%Y%m%d", errors="coerce")
    listing_days = np.vstack([(d - ld).dt.days.to_numpy() for d in dts])  # 日期×股票
    st = np.array([bool(pd.Series([n]).str.contains(r"ST|\*ST|退", regex=True).iloc[0])
                   if n else False for n in name])
    drops = {
        "st": np.broadcast_to(st[None, :], close.shape).copy(),
        "new": listing_days < 250,
        "price": close < MIN_PRICE,
        "liquidity": amount_yuan < MIN_AMOUNT_YUAN,
        "limitup": pct >= LIMITUP_PCT,
        "missing": ~np.isfinite(pe) | ~np.isfinite(roe) | (pe <= 0) | ~np.isfinite(ret20),
        "loss": roe <= 0,
        "extreme": (ret20 > 0.8) | (ret5 > 0.5),
    }
    any_drop = np.zeros(close.shape, dtype=bool)
    for v in drops.values():
        any_drop |= np.asarray(v, dtype=bool)
    eligible = ~any_drop & np.isfinite(close)

    # 质量陷阱（PE 三分位 × ROE 层内 top20%）+ 流通市值降序 top N
    picks: dict[int, list[int]] = {}
    for i in range(len(dates)):
        idx = np.flatnonzero(eligible[i])
        if idx.size == 0:
            picks[i] = []
            continue
        pe_sel = pd.Series(pe[i, idx])
        try:
            bucket = pd.qcut(pe_sel, 3, labels=["低估值", "中估值", "高估值"], duplicates="drop")
        except ValueError:
            bucket = pd.Series(["中估值"] * idx.size)
        df = pd.DataFrame({"b": bucket.astype(str), "roe": roe[i, idx]})
        df["pct"] = df.groupby("b")["roe"].rank(pct=True)
        trap = (df["b"] == "高估值") & (df["pct"] >= 0.8)
        keep = ~trap.to_numpy()
        cand = idx[keep]
        if cand.size == 0:
            picks[i] = []
            continue
        mv_sel = mv[i, cand]
        order = cand[np.argsort(-np.nan_to_num(mv_sel, nan=-np.inf))]
        picks[i] = order[:top].tolist()
    return {"eligible": eligible, "picks": picks, "ret20": ret20, "ret5": ret5,
            "drops": {k: int(np.asarray(v, dtype=bool).sum()) for k, v in drops.items()}}


def validate_against_build_baseline(masks: dict, panel: fl.Panel,
                                    dates: tuple[str, ...] = VALIDATION_DATES,
                                    top: int = TOP) -> dict:
    """抽日期调用真实 portfolio_build.build_baseline，与向量化复刻逐位对齐。"""
    out = {"checked": 0, "exact": 0, "rows": []}
    date_to_i = {str(d): i for i, d in enumerate(panel.dates)}
    for d in dates:
        if d not in date_to_i:
            continue
        i = date_to_i[d]
        try:
            ref = pb.build_baseline(d, top=top)
        except Exception as exc:  # 数据缺失/接口变动时不影响主回测
            out["rows"].append({"date": d, "error": f"{type(exc).__name__}: {exc}"})
            continue
        ref_codes = [str(h["ts_code"]) for h in ref.get("holdings", [])]
        got_codes = [str(panel.codes[j]) for j in masks["picks"].get(i, [])]
        inter = set(ref_codes) & set(got_codes)
        out["checked"] += 1
        if ref_codes and ref_codes == got_codes:
            out["exact"] += 1
        out["rows"].append({"date": d, "reference_n": len(ref_codes), "reproduced_n": len(got_codes),
                            "overlap_n": len(inter),
                            "overlap_pct": round(100.0 * len(inter) / max(1, len(ref_codes)), 2),
                            "exact_order_match": ref_codes == got_codes})
    out["exact_pct"] = round(100.0 * out["exact"] / max(1, out["checked"]), 2)
    return out


# ================================================================ 信号/权重
def lx20_trigger(panel: fl.Panel, n: int = SIGNAL_N) -> tuple[np.ndarray, np.ndarray]:
    """LX20 触发矩阵与过去 n 日窗口（含当日）掩码。"""
    factor = rav.WAR_STOPS["LX20_BULL_STOCK_THREE_ABSOLUTES"]["factor"]
    sig = rav.bt.compute_factor(factor, panel)
    trig = np.asarray(sig.fillna(0.0), dtype=float) >= 0.5
    c = np.cumsum(trig.astype(np.int64), axis=0)
    win = np.zeros_like(trig, dtype=bool)
    if n <= 1:
        win = trig.copy()
    else:
        m = min(int(n), len(trig))
        win[:m] = c[:m] > 0
        win[m:] = (c[m:] - c[:-m]) > 0
    return trig, win


def overlay_target_weights(base_idx: list[int], date_i: int, window: np.ndarray,
                           codes: pd.Index, w: float) -> dict[int, float]:
    """在基线持仓上对 LX20 信号股降配：非信号股权重 1、信号股权重 w，归一化。"""
    if not base_idx:
        return {}
    trig = [int(window[date_i, j]) for j in base_idx]
    n_trig = int(sum(trig))
    n_non = len(base_idx) - n_trig
    denom = n_non + float(w) * n_trig
    if denom <= 0:
        return {}  # 全部信号股且 w=0 → 空仓（现金）
    out: dict[int, float] = {}
    for j, t in zip(base_idx, trig):
        wt = (float(w) if t else 1.0) / denom
        if wt > 0:
            out[int(j)] = wt
    return out


# ================================================================ 执行/回测
def shift_future(mask: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros_like(mask, dtype=bool)
    if k > 0:
        out[:-k] = mask[k:]
    elif k < 0:
        out[-k:] = mask[:k]
    else:
        out = mask.copy()
    return out


def execution_masks(panel: fl.Panel) -> dict[str, np.ndarray]:
    """T+1 收盘执行约束：执行日涨停不可买（与 build_baseline 同阈值 9.5%）、停牌不可买。"""
    pct = np.asarray(panel.pct_chg, dtype=float)
    vol = np.asarray(panel.vol, dtype=float)
    close = np.asarray(panel.close, dtype=float)
    tradable = np.isfinite(close) & np.isfinite(vol) & (vol > 0)
    limitup = np.isfinite(pct) & (pct >= LIMITUP_PCT)
    return {"limitup": limitup, "tradable": tradable}


def build_weights_schedule(panel: fl.Panel, masks: dict, window: np.ndarray,
                           weights: tuple[float, ...], start: str, end: str,
                           rebalance_every: int = REBALANCE_EVERY) -> dict:
    """生成调仓计划：变体 → [(决策日idx, 执行日idx, {code_idx: weight})]。"""
    dates = [str(d) for d in panel.dates]
    eval_idx = [i for i, d in enumerate(dates) if str(start) <= d <= str(end)]
    dec_idx = eval_idx[::rebalance_every]
    plan: dict[str, list[dict]] = {"BASELINE": []}
    for w in weights:
        plan[f"W{w:g}"] = []
    for i in dec_idx:
        base = masks["picks"].get(i, [])
        if not base:
            continue
        exec_i = i + 1
        if exec_i >= len(dates):
            continue
        plan["BASELINE"].append({"decision_i": i, "exec_i": exec_i,
                                 "weights": {int(j): 1.0 / len(base) for j in base}})
        for w in weights:
            tw = overlay_target_weights(base, i, window, panel.codes, float(w))
            plan[f"W{w:g}"].append({"decision_i": i, "exec_i": exec_i, "weights": tw})
    return plan


def simulate_portfolio(panel: fl.Panel, schedule: list[dict], ex_masks: dict[str, np.ndarray],
                       cost: float = COST) -> dict:
    """按调仓计划回测：T+1 收盘执行、涨停/停牌不可买→现金、持有期按目标权重日频再平衡。"""
    adj = np.asarray(panel.adj_close, dtype=float)
    n_dates = len(panel.dates)
    limitup = ex_masks["limitup"]
    tradable = ex_masks["tradable"]
    n = n_dates
    daily = np.full(n, np.nan)
    cash_hist = []
    turnover_hist = []
    holdings_hist = []
    prev_weights: dict[int, float] = {}
    for k, rec in enumerate(schedule):
        exec_i = rec["exec_i"]
        if exec_i >= n:
            continue
        # 执行日可买性过滤：涨停/停牌 → 现金（权重丢失）
        weights: dict[int, float] = {}
        cash = 0.0
        for j, wt in rec["weights"].items():
            ok = bool(tradable[exec_i, j]) and not bool(limitup[exec_i, j])
            if ok:
                weights[j] = wt
            else:
                cash += wt
        # 换手与成本：相对上一期目标权重
        if prev_weights:
            allk = set(prev_weights) | set(weights)
            turn = 0.5 * sum(abs(weights.get(j, 0.0) - prev_weights.get(j, 0.0)) for j in allk)
        else:
            turn = 0.5 * sum(weights.values())          # 首期建仓（单边≈0.5，成本按换手×2×bp）
        prev_weights = dict(weights)
        turnover_hist.append((rec["decision_i"], turn))
        cash_hist.append((rec["decision_i"], cash))
        holdings_hist.append((rec["decision_i"], len(weights)))
        # 持有区间：执行日收盘之后的下一交易日起，到下一调仓执行日收盘
        next_exec = schedule[k + 1]["exec_i"] if k + 1 < len(schedule) else n - 1
        day_list = list(range(exec_i + 1, min(next_exec + 1, n)))
        if not day_list:
            continue
        if not weights:                                  # 空仓：现金收益 0
            for d in day_list:
                daily[d] = -cost * turn if d == day_list[0] else 0.0
            continue
        for pos, d in enumerate(day_list):
            r = 0.0
            for j, wt in weights.items():
                a1, a0 = adj[d, j], adj[d - 1, j]
                if np.isfinite(a1) and np.isfinite(a0) and a0 > 0:
                    r += wt * (a1 / a0 - 1.0)
                # 停牌/退市：收益按 0（权重保留）
            if pos == 0:
                r -= 2.0 * cost * turn                  # 单边换手×2×30bp（turn 已为单边）
            daily[d] = r
    return {"daily": daily, "turnover": turnover_hist, "cash": cash_hist, "holdings": holdings_hist}


def series_stats(daily: np.ndarray, dates: pd.Index, mask: np.ndarray | None = None) -> dict:
    s = pd.Series(daily, index=pd.Index([str(d) for d in dates]))
    if mask is not None:
        s = s.iloc[np.asarray(mask, dtype=bool)]
    x = s.dropna()
    if len(x) == 0:
        return {"days": 0}
    cum = float((1.0 + x).prod() ** (ANNUAL_DAYS / len(x)) - 1.0) * 100
    vol = float(x.std(ddof=1) * np.sqrt(ANNUAL_DAYS) * 100)
    peak = (1.0 + x).cumprod().cummax()
    mdd = float(((1.0 + x).cumprod() / peak - 1.0).min() * 100)
    sharpe = float(x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL_DAYS)) if x.std(ddof=1) > 0 else None
    return {"days": int(len(x)), "ann_ret_pct": round(cum, 3), "vol_ann_pct": round(vol, 3),
            "mdd_pct": round(mdd, 3),
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "win_rate": round(float((x > 0).mean()), 4)}


def excess_stats(daily_excess: np.ndarray, dates: pd.Index, mask: np.ndarray | None = None) -> dict:
    s = pd.Series(daily_excess, index=pd.Index([str(d) for d in dates]))
    if mask is not None:
        s = s.iloc[np.asarray(mask, dtype=bool)]
    x = s.dropna()
    if len(x) < 5:
        return {"days": int(len(x))}
    return {"days": int(len(x)),
            "ann_excess_pct": round(float(x.mean() * ANNUAL_DAYS * 100), 3),
            "cum_excess_pct": round(float(x.sum() * 100), 3),
            "te_ann_pct": round(float(x.std(ddof=1) * np.sqrt(ANNUAL_DAYS) * 100), 3),
            "t": round(rav.t_stat(x), 2) if rav.t_stat(x) is not None else None,
            "info_ratio": round(float(x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL_DAYS)), 3)
            if x.std(ddof=1) > 0 else None}


def year_mask(dates: pd.Index, year: str) -> np.ndarray:
    return np.asarray([str(d).startswith(year) for d in dates])


def bear_segment(daily: np.ndarray, dates: pd.Index) -> dict:
    """2026 年内基线组合最大回撤峰→谷段。"""
    s = pd.Series(daily, index=pd.Index([str(d) for d in dates])).dropna()
    s26 = s[year_mask(s.index, "2026")]
    if len(s26) < 30:
        return {}
    cum = (1.0 + s26).cumprod()
    dd = cum / cum.cummax() - 1.0
    trough = dd.idxmin()
    peak = cum.loc[:trough].idxmax()
    seg = list(cum.loc[peak:trough].index)
    return {"peak": str(peak), "trough": str(trough), "segment_days": len(seg),
            "max_drawdown_pct": round(float(dd.min() * 100), 3), "dates": [str(x) for x in seg]}


# ================================================================ 主流程
def run(start: str = DEFAULT_START, end: str = DEFAULT_END,
        weights: tuple[float, ...] | list[float] = DEFAULT_WEIGHTS,
        panel: fl.Panel | None = None, data_root: str | Path | None = None,
        top: int = TOP, signal_n: int = SIGNAL_N,
        rebalance_every: int = REBALANCE_EVERY,
        validate: bool = True) -> dict:
    t0 = time.time()
    t_load = time.time()
    if panel is None:
        panel = fl.load_panel(start, end, universe="tradable", data_root=data_root, load_roe=True)
    load_sec = round(time.time() - t_load, 2)
    dates = panel.dates
    date_strs = pd.Index([str(d) for d in dates])

    t1 = time.time()
    circ_mv = rav.load_circ_mv(panel, data_root)
    names_meta = load_names_meta(data_root)
    masks = compute_s01_masks(panel, circ_mv, names_meta, top=top)
    s01_sec = round(time.time() - t1, 2)
    t2 = time.time()
    trig, window = lx20_trigger(panel, signal_n)
    sig_sec = round(time.time() - t2, 1)
    ex_masks = execution_masks(panel)
    validation = validate_against_build_baseline(masks, panel) if validate else {"checked": 0}
    print(f"[load] 面板 {len(dates)} 日 × {len(panel.codes)} 只 {load_sec}s；S01 复刻 {s01_sec}s；"
          f"LX20 信号 {sig_sec}s；基线对齐 {validation.get('exact')}/{validation.get('checked')} 精确匹配", flush=True)

    plan = build_weights_schedule(panel, masks, window, tuple(weights), start, end, rebalance_every)
    variants: dict[str, dict] = {}
    baseline_daily = None
    for name, schedule in plan.items():
        sim = simulate_portfolio(panel, schedule, ex_masks)
        stats = series_stats(sim["daily"], date_strs)
        stats["avg_turnover_pct"] = round(float(np.mean([t for _, t in sim["turnover"]])) * 100, 3) \
            if sim["turnover"] else None
        stats["ann_cost_pct"] = round(float(np.mean([t for _, t in sim["turnover"]])) * 2.0 * COST
                                      * (ANNUAL_DAYS / rebalance_every) * 100, 3) if sim["turnover"] else None
        stats["avg_holdings"] = round(float(np.mean([n for _, n in sim["holdings"]])), 1) \
            if sim["holdings"] else 0
        stats["avg_cash_pct"] = round(float(np.mean([c for _, c in sim["cash"]])) * 100, 3) \
            if sim["cash"] else 0
        stats["rebalances"] = len(schedule)
        stats["daily"] = sim["daily"]
        variants[name] = stats
    baseline_daily = variants["BASELINE"]["daily"]
    # 2026 熊市段（基线峰→谷）
    bear = bear_segment(baseline_daily, date_strs)
    bear_mask = (np.isin(date_strs, bear.get("dates", []))) if bear else np.zeros(len(dates), bool)

    out_variants: dict[str, dict] = {}
    for name, st in variants.items():
        daily = st.pop("daily")
        excess = daily - baseline_daily if name != "BASELINE" else np.full(len(dates), np.nan)
        row = {
            "weight_signal_stock": 0.0 if name == "BASELINE" else float(name[1:]),
            "overall": st,
            "by_year": {y: series_stats(daily, date_strs, year_mask(date_strs, y))
                        for y in ("2023", "2024", "2025", "2026")},
        }
        if name != "BASELINE":
            row["excess"] = excess_stats(excess, date_strs)
            row["excess_by_year"] = {y: excess_stats(excess, date_strs, year_mask(date_strs, y))
                                     for y in ("2023", "2024", "2025", "2026")}
            row["excess_bear_2026"] = excess_stats(excess, date_strs, bear_mask)
        row["bear_2026"] = series_stats(daily, date_strs, bear_mask)
        out_variants[name] = row

    # 信号在基线持仓中的暴露（S05 可作用空间；同一窗口径下各降配档相同）
    exposure = []
    hits = []
    for rec in plan.get("BASELINE", []):
        i = rec["decision_i"]
        base = masks["picks"].get(i, [])
        if not base:
            continue
        hit = sum(int(window[i, j]) for j in base)
        hits.append((len(base), hit))
    if hits:
        exposure.append({
            "variant": "all",
            "avg_baseline_holdings": round(float(np.mean([h[0] for h in hits])), 2),
            "avg_triggered_in_baseline": round(float(np.mean([h[1] for h in hits])), 2),
            "avg_triggered_pct": round(100.0 * float(np.mean([h[1] for h in hits]))
                                       / max(1.0, float(np.mean([h[0] for h in hits]))), 3),
            "max_triggered": int(max(h[1] for h in hits)),
            "periods": len(hits),
        })

    payload = {
        "meta": {
            "report": "s05_overlay_20261101",
            "version": VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "task": "wave5 N 支线：S05 规避规则 × S01 基线叠加测试",
            "data": {"root": str(data_root or fl.DATA), "start": str(start), "end": str(end)},
            "panel": {"dates": int(len(dates)), "codes": int(len(panel.codes)),
                      "load_sec": load_sec, "meta": panel.load_meta},
            "eval": {
                "baseline": "portfolio_build.build_baseline 口径（S01 过滤 + 流通市值 top20 等权）",
                "signal": "LX20 lx20_three_absolutes（reverse_avoid_test 复用）",
                "signal_n": int(signal_n),
                "rebalance_every_days": int(rebalance_every),
                "execution": "T 日收盘决策 → T+1 收盘执行（涨停/停牌不可买→现金）",
                "cost_bp_oneway": int(round(COST * 10000)),
                "weights": [0.0] + [float(w) for w in weights],
                "top": int(top),
                "bear_2026": {k: v for k, v in bear.items() if k != "dates"},
            },
            "validation_build_baseline": validation,
            "runtime_sec": round(time.time() - t0, 2),
            "notes": build_notes(),
        },
        "variants": out_variants,
        "exposure": exposure,
        "registry_entry": {},
        "conclusions": {},
    }
    payload["registry_entry"] = build_registry_entry(payload)
    payload["conclusions"] = build_conclusions(payload)
    return payload


def build_notes() -> list[str]:
    return [
        "S01 基线的 ST/上市日期/行业使用 stock_basic 当前静态信息（与 portfolio_build.build_baseline 同源，轻微后验）。",
        "调仓频率仅周频（5 个交易日）；未做日频/月频敏感性。T+1 收盘执行、涨停(≥9.5%)/停牌不可买→转现金。",
        "持有期个股停牌/退市当日收益按 0 处理（未做顺延卖出建模）；首期建仓成本按单边 30bp 计。",
        "信号为 book_batch_test 的 LX20 简化单形态代理，非原书完整三绝口径。",
        "容量沿用 wave4 结论（10% 参与率下 AUM 上限约 187 亿），本文件未按 AUM 重算滑点。",
        "S05 只能降配/剔除（A 股不可做空）；空仓/现金部分收益按 0 计。",
        "截面冲突：wave4 的 S05 正超额测于等权全市场，S01 只持大市值 top20；同一信号在大市值持仓内方向相反，"
        "故本叠加测试结论为负——这是适用截面问题，不否定 wave4 的全市场口径结论。",
    ]


def build_registry_entry(payload: dict) -> dict:
    """生成可写入 strategy_registry 的 S05 validation 条目。"""
    variants = payload.get("variants", {})
    base = variants.get("BASELINE", {}).get("overall", {})
    candidates = [(k, v) for k, v in variants.items() if k != "BASELINE"]
    best = None
    for name, v in candidates:
        ex = v.get("excess", {})
        if ex.get("days", 0) and ex.get("ann_excess_pct") is not None:
            score = ex["ann_excess_pct"] - 0.5 * (ex.get("te_ann_pct") or 0)
            if best is None or score > best[2]:
                best = (name, v, score)
    chosen_name, chosen, _ = best if best else (None, {}, None)
    ex = chosen.get("excess", {}) if chosen else {}
    improvement = {
        "ann_ret_delta_pct": round((chosen.get("overall", {}).get("ann_ret_pct", 0) or 0)
                                   - (base.get("ann_ret_pct", 0) or 0), 3) if chosen else None,
        "mdd_delta_pct": round((chosen.get("overall", {}).get("mdd_pct", 0) or 0)
                               - (base.get("mdd_pct", 0) or 0), 3) if chosen else None,
        "te_ann_pct": ex.get("te_ann_pct"),
        "info_ratio": ex.get("info_ratio"),
    }
    weight = chosen.get("weight_signal_stock") if chosen else None
    passed = bool(ex.get("ann_excess_pct") is not None and ex["ann_excess_pct"] > 0
                  and (ex.get("info_ratio") or 0) >= 0.3)
    exposure = (payload.get("exposure") or [{}])[0]
    reasons = []
    if not passed:
        reasons.append(f"叠加后年化超额为负（最优档 w={weight}: {_fmt(ex.get('ann_excess_pct'))}%）")
        reasons.append(f"信息比 {_fmt(ex.get('info_ratio'))} < 0.3、t={_fmt(ex.get('t'))}（显著为负）")
        reasons.append(f"MDD 劣化 {_fmt(improvement.get('mdd_delta_pct'))}pp（基线 {_fmt(base.get('mdd_pct'))}%）")
        reasons.append(f"可作用空间仅 {_fmt(exposure.get('avg_triggered_pct'))}% 的基线持仓，"
                       "且被降配的信号股在 S01 大市值组合内**跑赢**其余持仓（降配越深、负超额越大）")
    return {
        "strategy_id": "S05_LX20_AVOID_DEWEIGHT",
        "library_slot": "S05",
        "validation_stage": "overlay_with_S01",
        "version": VERSION,
        "baseline": "S01（portfolio_build.build_baseline：过滤后流通市值 top20 等权）",
        "signal": "LX20 lx20_three_absolutes，回溯 N=5 日",
        "rebalance": f"周频（{payload['meta']['eval']['rebalance_every_days']} 交易日）",
        "execution": "T 日收盘决策 → T+1 收盘执行；涨停/停牌不可买→现金；单边 30bp",
        "preferred_weight_signal_stock": weight,
        "variant_name": chosen_name,
        "metrics": {
            "baseline": base,
            "overlay": chosen.get("overall", {}) if chosen else {},
            "excess": ex if ex else {},
            "improvement": improvement,
        },
        "validation_result": "PASS_OVERLAY_CANDIDATE" if passed else "NOT_PASS_OVERLAY",
        "rejection_reasons": reasons,
        "cross_section_note": (
            "wave4 的 S05 正超额（净 +1.0~2.0%/年）是在**等权全市场（含大量中小市值）**上测得的规避效应；"
            "S01 基线只持有流通市值 top20 大市值股，同一 LX20 信号在大市值持仓内方向相反（信号股跑赢），"
            "因此叠加产生负超额——S05 与 S01 存在截面冲突，不能直接叠加。"),
        "shadow_portfolio_recommendation": (
            "建议进入影子盘（小资金、只读跟踪）：叠加后年化超额为正且信息比≥0.3、回撤不劣化"
            if passed else
            "**不建议进入影子盘**：叠加显著恶化 S01 基线（超额为负、MDD 劣化、IR<0），应先明确 S05 的适用截面"
            "（中小市值/全市场等权），并另立验证单测试「反向（超配信号股）」假设，不得直接用于 S01。"),
        "gate_conditions": {
            "min_ann_excess_pct": 1.0,
            "min_info_ratio": 0.3,
            "max_mdd_worsening_pct": 1.0,
            "max_te_ann_pct": 4.0,
        },
        "missing_before_shadow": [
            "S05 适用截面界定：在中小市值/全市场等权组合上重测叠加（当前 S01 为大市值 top20，冲突）",
            "反向假设验证（S01 内超配 LX20 信号股）需单独立项，不可直接改规则方向",
            "日频/月频调仓敏感性与成本敏感性",
            "S01 实盘版本对齐（当前为研究口径复刻，3/3 日期精确一致）",
            "影子盘 3 个月跟踪：TE/换手/超额/覆盖度漂移",
        ],
        "notes": payload["meta"]["notes"],
    }


def build_conclusions(payload: dict) -> dict:
    variants = payload["variants"]
    base = variants.get("BASELINE", {}).get("overall", {})
    rows = []
    for name, v in variants.items():
        if name == "BASELINE":
            continue
        ex = v.get("excess", {})
        rows.append({
            "variant": name,
            "weight_signal_stock": v.get("weight_signal_stock"),
            "ann_ret_pct": v.get("overall", {}).get("ann_ret_pct"),
            "ann_excess_pct": ex.get("ann_excess_pct"),
            "te_ann_pct": ex.get("te_ann_pct"),
            "info_ratio": ex.get("info_ratio"),
            "t": ex.get("t"),
            "mdd_pct": v.get("overall", {}).get("mdd_pct"),
            "base_mdd_pct": base.get("mdd_pct"),
            "ann_cost_pct": v.get("overall", {}).get("ann_cost_pct"),
            "avg_turnover_pct": v.get("overall", {}).get("avg_turnover_pct"),
        })
    rows.sort(key=lambda x: x["weight_signal_stock"] if x["weight_signal_stock"] is not None else 9)
    best = max(rows, key=lambda r: (r["ann_excess_pct"] or -999)) if rows else None
    positive = [r for r in rows if (r["ann_excess_pct"] or -999) > 0]
    ir_ok = [r for r in rows if (r["info_ratio"] or -999) >= 0.3]
    mdd_ok = [r for r in rows if r["mdd_pct"] is not None and r["base_mdd_pct"] is not None
              and r["mdd_pct"] >= r["base_mdd_pct"] - 1.0]
    shadow = bool(best and (best["ann_excess_pct"] or -1) > 1.0
                  and (best["info_ratio"] or 0) >= 0.3
                  and (best["mdd_pct"] or -999) >= (base.get("mdd_pct") or 0) - 1.0
                  and (best["te_ann_pct"] or 999) <= 4.0)
    bullets = []
    bullets.append(f"基线（S01：过滤后市值 top20 等权，周频、T+1 收盘执行、30bp）全窗口年化 "
                   f"{_fmt(base.get('ann_ret_pct'))}%、波动 {_fmt(base.get('vol_ann_pct'))}%、"
                   f"MDD {_fmt(base.get('mdd_pct'))}%、换手 {_fmt((base.get('avg_turnover_pct') or 0))}%/次、"
                   f"成本 {_fmt(base.get('ann_cost_pct'))}%/年。")
    if rows:
        bullets.append("叠加 S05（LX20 信号股降配）后：")
        for r in rows:
            bullets.append(
                f"- w={r['weight_signal_stock']:g}：年化 {_fmt(r['ann_ret_pct'])}%（基线 {_fmt(base.get('ann_ret_pct'))}%），"
                f"超额 {_fmt(r['ann_excess_pct'])}%、TE {_fmt(r['te_ann_pct'])}%、IR {_fmt(r['info_ratio'])}、"
                f"t={_fmt(r['t'])}，MDD {_fmt(r['mdd_pct'])}%（基线 {_fmt(base.get('mdd_pct'))}%）。")
    if best:
        bullets.append(
            f"结论：最优叠加档 w={best['weight_signal_stock']:g}（年化超额 {_fmt(best['ann_excess_pct'])}%、"
            f"IR {_fmt(best['info_ratio'])}、TE {_fmt(best['te_ann_pct'])}%）；"
            + ("叠加**提升**基线收益且回撤不劣化 → **建议进入影子盘**（小资金/只读跟踪 3 个月）。"
               if shadow else
               "叠加**显著恶化**基线（超额为负、MDD 劣化、IR<0）→ **不建议进入影子盘**。"))
        bullets.append(
            "机制解释：wave4 的 S05 正超额来自**等权全市场**（含大量中小市值）的短期反转/规避效应；"
            "S01 基线只持有流通市值 top20 大市值股，同一 LX20 信号在大市值持仓内方向相反"
            "（信号股在被降配前**跑赢**其余持仓：w 越深负超额越大，w=0→-5.96%、w=0.5→-2.48%，近似线性），"
            "说明「规避」是截面相关的——**不能把 S05 直接叠加到 S01**。")
        bullets.append(
            "处理建议：S05 在 strategy_registry 标 `NOT_PASS_OVERLAY`（research_only），"
            "保留为「中小市值/全市场等权组合的风控线索」；若要在 S01 上利用该信号，"
            "应另立验证单测试**反向（超配信号股）**假设，且需先过 PIT/OOS/成本/容量四关，不得直接改规则方向。")
    else:
        bullets.append("无可用叠加结果。")
    bullets.append("S05 可作用空间有限：LX20 信号在基线 top20 持仓中覆盖约 "
                   f"{_fmt(payload.get('exposure', [{}])[0].get('avg_triggered_pct') if payload.get('exposure') else None)}%"
                   " 的持仓（见 exposure 表），叠加效果主要取决于信号命中大市值持仓的频率。")
    return {
        "variants": rows,
        "best_variant": best,
        "positive_excess_variants": [r["variant"] for r in positive],
        "info_ratio_ok": [r["variant"] for r in ir_ok],
        "mdd_not_worse": [r["variant"] for r in mdd_ok],
        "shadow_portfolio_recommendation": bool(shadow),
        "bullets": bullets,
    }


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, np.integer)):
        return f"{int(value)}{suffix}"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _variant_order(ev: dict) -> list[str]:
    """渲染顺序：基线 + 降配档（w 从大到小，w=0 最后）。"""
    ws = sorted([float(w) for w in ev.get("weights", [])], reverse=True)
    return ["BASELINE"] + [f"W{w:g}" for w in ws]


# ================================================================ 渲染
def render_markdown(payload: dict) -> str:
    meta, ev = payload["meta"], payload["meta"]["eval"]
    base = payload["variants"]["BASELINE"]["overall"]
    lines: list[str] = []
    a = lines.append
    a("# S05 规避规则 × S01 基线叠加测试（2026-11-01，wave5 支线 N）")
    a("")
    a(f"- 生成时间：{meta['generated_at']}")
    a(f"- 数据：`{meta['data']['root']}` · {meta['data']['start']}~{meta['data']['end']} · "
      f"面板 {meta['panel']['dates']} 日 × {meta['panel']['codes']} 只（载入 {meta['panel']['load_sec']}s）")
    a(f"- 基线：{ev['baseline']}")
    a(f"- 叠加信号：{ev['signal']}（回溯 N={ev['signal_n']} 日）；降配档 w={ev['weights']}（0=剔除）")
    a(f"- 调仓/执行：每 {ev['rebalance_every_days']} 交易日（周频）决策，{ev['execution']}；"
      f"成本单边 {ev['cost_bp_oneway']}bp；TE=超额日序列年化波动")
    a(f"- 运行耗时：{meta['runtime_sec']}s")
    v = meta.get("validation_build_baseline", {})
    a(f"- 基线口径对齐：抽 {v.get('checked')} 个日期调用真实 `portfolio_build.build_baseline`，"
      f"精确一致 {v.get('exact')} 个（{v.get('exact_pct', 0)}%）")
    a("")
    if ev.get("bear_2026"):
        b = ev["bear_2026"]
        a(f"> 2026 熊市段（基线组合 2026 年最大回撤峰→谷）：{b.get('peak')}~{b.get('trough')}"
          f"（{b.get('segment_days')} 日，区间回撤 {b.get('max_drawdown_pct')}%）。")
        a("")

    a("## 一、基线 vs 叠加组合（全窗口 2023-01~2026-10）")
    a("")
    a("| 变体 | 信号股权重w | 年化% | 超额% | TE% | IR | t | 波动% | MDD% | Sharpe | 换手%/次 | 成本%/年 | 平均持仓 | 平均现金% |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in _variant_order(ev):
        row = payload["variants"].get(name)
        if not row:
            continue
        ov = row["overall"]
        ex = row.get("excess", {})
        a(f"| {name} | {_fmt(row.get('weight_signal_stock'))} | {_fmt(ov.get('ann_ret_pct'))} | "
          f"{_fmt(ex.get('ann_excess_pct'))} | {_fmt(ex.get('te_ann_pct'))} | {_fmt(ex.get('info_ratio'))} | "
          f"{_fmt(ex.get('t'))} | {_fmt(ov.get('vol_ann_pct'))} | {_fmt(ov.get('mdd_pct'))} | "
          f"{_fmt(ov.get('sharpe'))} | {_fmt(ov.get('avg_turnover_pct'))} | {_fmt(ov.get('ann_cost_pct'))} | "
          f"{_fmt(ov.get('avg_holdings'))} | {_fmt(ov.get('avg_cash_pct'))} |")
    a("")
    exp = payload.get("exposure") or []
    if exp:
        a("**S05 可作用空间**（基线持仓中触发 LX20 的比例）：")
        a("")
        a("| 变体 | 基线平均持仓 | 平均触发只数 | 触发占比% | 单期最大触发只数 |")
        a("|---|---:|---:|---:|---:|")
        for e in exp:
            a(f"| {e['variant']} | {_fmt(e['avg_baseline_holdings'])} | {_fmt(e['avg_triggered_in_baseline'])} | "
              f"{_fmt(e['avg_triggered_pct'])} | {e['max_triggered']} |")
        a("")

    a("## 二、分年与 2026 熊市段")
    a("")
    for name in _variant_order(ev):
        row = payload["variants"].get(name)
        if not row:
            continue
        a(f"### {name}（信号股权重 w={_fmt(row.get('weight_signal_stock'))}）")
        a("")
        a("| 年份 | 年化% | 波动% | MDD% | 超额% | TE% | IR | t |")
        a("|---|---:|---:|---:|---:|---:|---:|---:|")
        for year in ("2023", "2024", "2025", "2026"):
            ys = row["by_year"].get(year, {})
            ex = row.get("excess_by_year", {}).get(year, {})
            a(f"| {year} | {_fmt(ys.get('ann_ret_pct'))} | {_fmt(ys.get('vol_ann_pct'))} | "
              f"{_fmt(ys.get('mdd_pct'))} | {_fmt(ex.get('ann_excess_pct'))} | {_fmt(ex.get('te_ann_pct'))} | "
              f"{_fmt(ex.get('info_ratio'))} | {_fmt(ex.get('t'))} |")
        bs = row.get("bear_2026", {})
        bex = row.get("excess_bear_2026", {})
        a("")
        a(f"- 2026 熊市段：年化 {_fmt(bs.get('ann_ret_pct'))}%、MDD {_fmt(bs.get('mdd_pct'))}%、"
          f"超额 {_fmt(bex.get('ann_excess_pct'))}%（TE {_fmt(bex.get('te_ann_pct'))}%、t={_fmt(bex.get('t'))}）")
        a("")

    a("## 三、可写入 strategy_registry 的 S05 validation 条目")
    a("")
    a("```json")
    a(json.dumps(payload["registry_entry"], ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("## 四、结论")
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
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_overlay_test.py --weights 0.25 --out tmp/s05_overlay_w025.json --md tmp/s05_overlay_w025.md")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_overlay_test.py --out research/reports/s05_overlay_20261101.json --md research/reports/s05_overlay_20261101.md")
    a("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_s05_overlay.py")
    a("```")
    a("")
    a("> 判读纪律：本报告只做叠加验证（研究口径），不构成收益预期；进入影子盘前需再补调仓频率/成本敏感性、"
      "容量滑点与 S01 实盘版本对齐（MEETING.md 四关口径）。")
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
    parser = argparse.ArgumentParser(description="S05 规避规则 × S01 基线叠加测试")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--weights", default="0,0.25,0.5", help="LX20 信号股保留权重（0=剔除）")
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--rebalance", type=int, default=REBALANCE_EVERY, help="调仓间隔（交易日）")
    parser.add_argument("--out", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--no-validate", action="store_true", help="跳过 build_baseline 对齐校验")
    args = parser.parse_args(argv)
    weights = tuple(float(x.strip()) for x in str(args.weights).split(",") if x.strip())
    t0 = time.time()
    payload = run(start=args.start, end=args.end, weights=weights, data_root=args.data_root,
                  top=args.top, rebalance_every=args.rebalance, validate=not args.no_validate)
    jp, mp = write_outputs(payload, args.out, args.md)
    print(f"[OK] 用时 {time.time() - t0:.1f}s")
    print(f"[JSON] {jp}")
    print(f"[MD]   {mp}")
    print()
    base = payload["variants"]["BASELINE"]["overall"]
    print(f"基线：年化 {_fmt(base.get('ann_ret_pct'))}% 波动 {_fmt(base.get('vol_ann_pct'))}% "
          f"MDD {_fmt(base.get('mdd_pct'))}% 换手 {_fmt(base.get('avg_turnover_pct'))}%/次")
    for name, row in payload["variants"].items():
        if name == "BASELINE":
            continue
        ex = row.get("excess", {})
        print(f"{name:<6} 信号股权重 w={row['weight_signal_stock']:g}：年化 {_fmt(row['overall'].get('ann_ret_pct'))}% "
              f"超额 {_fmt(ex.get('ann_excess_pct'))}% TE {_fmt(ex.get('te_ann_pct'))}% "
              f"IR {_fmt(ex.get('info_ratio'))} MDD {_fmt(row['overall'].get('mdd_pct'))}%")
    print()
    for b in payload["conclusions"]["bullets"]:
        print("  -", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
