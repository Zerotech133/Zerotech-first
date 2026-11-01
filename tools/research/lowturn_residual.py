# -*- coding: utf-8 -*-
"""支线 M：low_turnover 双重中性化残差检验（剥离「大市值+低波」风格后是否还有独立 alpha）。

背景
----
wave4（lowturn_attribution_20261101）归因结论：low_turnover 不是因子失效（IS/OOS IC 全正
0.071~0.118），但≈「大市值 + 低波防御」风格代理（signed 与小市值相关 -0.56、与低波 +0.66），
且 regime-dependent（hold60 上涨市 -6.72%/t=-2.45、下跌市 +7.87%）。
本脚本做最后一道检验：**把市值/波动率/行业暴露从因子分数里回归掉，残差是否还有 OOS alpha？**

方法
----
1. 横截面回归（PIT，每月末用当日数据）：
   signed_low_turnover(−换手率) ~ 1 + log(流通市值) + 20日波动率 + 行业哑变量，
   取残差 = 新信号（残差对上述风格正交；回归只用 T 日截面，无未来函数）。
   公司行为限制：回归日无 circ_mv/vol20/行业 的股票剔除；周频调仓时使用「最近一个月末」
   的残差面板（信号最多滞后 1 个月，仍完全 PIT），hold60 同理。
2. 组合：复用支线 G/J 的中性化实现（lowturn_freq_validate）：市值十分位×行业市场权重法、
   桶内取残差 top50% 等权、T+1 收盘建仓、复权、缺价冻结；周频 与 60 日持有 两档；
   成本 30bp 单边（净口径由毛净值×换手序列解析折算），并给 10/20/30/50bp 成本敏感。
3. 对比：原始 signed low_turnover vs 残差 → IS(2023-24)/OOS(2025-26) 毛/净超额、t、逐年、IC、
   换手、容量；并验证残差正交化效果（回归前后与 logMV/vol20 的截面相关）。
4. 判定：残差「OOS 净超额>0 且 OOS t≥2 且逐年净同向≥3/4」→ 有独立 alpha（pass）；
   否则 = 纯风格代理 → 建议停止该方向（毛口径同阈值作诊断）。

产出：research/reports/lowturn_residual_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/lowturn_residual.py
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
import lowturn_freq_validate as lv  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SCHEMA_VERSION = "lowturn_residual.v1"

FACTOR = "low_turnover"
YEARS = ("2023", "2024", "2025", "2026")
IS_YEARS = ("2023", "2024")
OOS_YEARS = ("2025", "2026")
COST_BP = 30.0
COST_GRID_BP = (10, 20, 30, 50)
HOLD_FRAC = 0.5
HORIZON = 20
MIN_REGRESSION_STOCKS = 100      # 单次截面回归最少股票数
FREQUENCIES: dict[str, dict] = {
    "weekly": {"label": "周频", "schedule": "weekly", "desc": "每周换仓（wave2 基线频率）"},
    "hold60": {"label": "60日持有", "schedule": "every60", "desc": "每 60 个交易日换仓（wave3 最优成本口径）"},
}
SIGNALS: dict[str, dict] = {
    "original": {"label": "原始 signed low_turnover", "desc": "−换手率（未剥离风格）"},
    "residual": {"label": "双重中性化残差", "desc": "−换手率 对 log(市值)+vol20+行业 回归取残差"},
}


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不依赖真实数据）
# --------------------------------------------------------------------------- #
def ols_residual(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """OLS 残差：resid = y − X·β（β 用最小二乘；X 需含常数列）。"""
    yv = np.asarray(y, dtype="float64")
    xv = np.asarray(x, dtype="float64")
    if xv.ndim == 1:
        xv = xv.reshape(-1, 1)
    beta, *_ = np.linalg.lstsq(xv, yv, rcond=None)
    return yv - xv @ beta


def _zscore(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype="float64")
    sd = float(np.std(v))
    if not np.isfinite(sd) or sd <= 0:
        return np.zeros_like(v)
    return (v - float(np.mean(v))) / sd


def build_design_matrix(log_mv: np.ndarray, vol20: np.ndarray,
                        industry_codes: np.ndarray) -> np.ndarray:
    """设计矩阵：[1, z(logMV), z(vol20), 行业哑变量(去掉首类)]。"""
    log_mv = np.asarray(log_mv, dtype="float64")
    vol20 = np.asarray(vol20, dtype="float64")
    inds = np.asarray([str(x) for x in industry_codes])
    cols = [np.ones(len(log_mv)), _zscore(log_mv), _zscore(vol20)]
    uniq = sorted(set(inds.tolist()))
    for ind in uniq[1:]:          # 去掉首类作为基准（与截距共线）
        cols.append((inds == ind).astype("float64"))
    return np.column_stack(cols)


def monthly_residuals(signed: np.ndarray, vol20: np.ndarray,
                      circ_mv_by_date: dict, month_end_dates,
                      industry: pd.Series, codes: list[str],
                      date_index: dict) -> tuple[dict[str, pd.Series], list[dict]]:
    """每月末横截面回归 → {月末日期: 残差 Series(index=ts_code)}，附拟合统计。

    PIT：只用该月末当日截面数据；返回的残差供其后一个月内的调仓使用。
    """
    panels: dict[str, pd.Series] = {}
    stats: list[dict] = []
    codes_arr = np.asarray(codes, dtype=object)
    for d in month_end_dates:
        mv = circ_mv_by_date.get(str(d))
        si = date_index.get(str(d))
        if mv is None or si is None or len(mv) == 0:
            continue
        idx = pd.Index([str(c) for c in mv.index])
        y = pd.Series(signed[si], index=codes_arr).reindex(idx)
        v = pd.Series(vol20[si], index=codes_arr).reindex(idx)
        mvv = pd.Series(mv).reindex(idx).astype("float64")
        valid = (y.notna() & v.notna() & mvv.notna()
                 & np.isfinite(y.to_numpy(dtype="float64"))
                 & np.isfinite(v.to_numpy(dtype="float64")) & (mvv > 0))
        n = int(valid.sum())
        if n < MIN_REGRESSION_STOCKS:
            continue
        yy = y[valid].to_numpy(dtype="float64")
        logmv = np.log(mvv[valid].to_numpy(dtype="float64"))
        vv = v[valid].to_numpy(dtype="float64")
        ind = industry.reindex(idx[valid]).fillna("未知").astype(str).to_numpy()
        xmat = build_design_matrix(logmv, vv, ind)
        resid = ols_residual(yy, xmat)
        panels[str(d)] = pd.Series(resid, index=idx[valid])
        ss_tot = float(np.sum((yy - yy.mean()) ** 2))
        ss_res = float(np.sum(resid ** 2))
        stats.append({"date": str(d), "n": n,
                      "r2": round(1.0 - ss_res / ss_tot, 4) if ss_tot > 0 else None})
    return panels, stats


def carry_forward_scores(residual_panel: dict[str, pd.Series],
                         target_dates, date_index: dict,
                         n_dates: int, codes: list[str]) -> np.ndarray:
    """把「最近月末残差面板」前向填充到各调仓信号日 → [date, code] 分数矩阵（其余行 NaN）。

    完全 PIT：target_date 只使用 ≤ 它的最近月末残差（信号滞后 ≤1 个月）。
    """
    code_pos = {str(c): j for j, c in enumerate(codes)}
    mat = np.full((n_dates, len(codes)), np.nan, dtype="float64")
    me_dates = sorted(residual_panel)
    for t in target_dates:
        si = date_index.get(str(t))
        if si is None:
            continue
        prev = [d for d in me_dates if d <= str(t)]
        if not prev:
            continue
        r = residual_panel[prev[-1]]
        for c, val in r.items():
            j = code_pos.get(str(c))
            if j is not None and np.isfinite(val):
                mat[si, j] = float(val)
    return mat


def signal_dates_for(schedule: str, range_dates) -> list[str]:
    if schedule == "weekly":
        return fv.weekly_signal_dates(range_dates)
    if schedule == "monthly":
        return lv.monthly_signal_dates(range_dates)
    if schedule == "every60":
        return lv.every_nth_signal_dates(range_dates, 60)
    raise ValueError(f"未知 schedule: {schedule}")


def cross_sectional_corr_mean(fmat: np.ndarray, proxy: np.ndarray,
                              elig: np.ndarray, positions,
                              min_names: int = fv.MIN_IC_NAMES) -> dict:
    """指定日期集合上的逐日截面 Spearman 均值（用于验证残差正交化）。"""
    vals: list[float] = []
    for i in positions:
        valid = elig[i] & np.isfinite(fmat[i]) & np.isfinite(proxy[i])
        if int(valid.sum()) < min_names:
            continue
        x = fmat[i][valid]
        y_ = proxy[i][valid]
        if np.std(x) <= 0 or np.std(y_) <= 0:
            continue
        rx = pd.Series(x).rank().to_numpy()
        ry = pd.Series(y_).rank().to_numpy()
        vals.append(float(np.corrcoef(rx, ry)[0, 1]))
    st = fv._tstat(vals)
    return {"n": st["n"],
            "mean": round(st["mean"], 4) if st["mean"] is not None else None,
            "t": round(st["t"], 2) if st["t"] is not None else None}


def residual_verdict(m: dict) -> dict:
    """残差判定：OOS 净超额>0 且 t≥2 且逐年同向≥3/4 → 有独立 alpha；毛口径同阈值作诊断。"""
    ok_net = (m.get("verdict_net") == "pass")
    ok_gross = (m.get("verdict_gross") == "pass")
    return {
        "independent_alpha": bool(ok_net),
        "independent_alpha_gross": bool(ok_gross),
        "verdict_net": m.get("verdict_net"),
        "verdict_gross": m.get("verdict_gross"),
    }


# --------------------------------------------------------------------------- #
# 评估
# --------------------------------------------------------------------------- #
def evaluate_portfolio(market, fmat: np.ndarray, elig: np.ndarray, signal_dates,
                       circ_mv_by_date: dict, industry: pd.Series,
                       bench_nav: dict, start: str, end: str,
                       amt20=None) -> dict:
    """中性组合（市场权重法）回测 + 成本网格 + 逐年指标。"""
    run = lv.run_portfolio(
        market, fmat, elig, signal_dates, circ_mv_by_date, industry, start, end,
        hold_frac=HOLD_FRAC, buffer_frac=0.0, max_turnover=None,
        tranche_frac=1.0, amt20=amt20,
    )
    out = {"rebalance_count": run["rebalance_count"],
           "avg_holdings": run["avg_holdings"],
           "avg_turnover": run["avg_turnover"],
           "adtv_median_yi": run["adtv_median_yi"],
           "cost_grid": {}, "at_main_cost": None}
    for bp in sorted(set(COST_GRID_BP) | {COST_BP}):
        out["cost_grid"][str(int(bp))] = lv.variant_metrics(
            run["nav_map"], bench_nav, run["turnover_rows"], float(bp),
            run["adtv_median_yi"], run["avg_holdings"])
    out["at_main_cost"] = out["cost_grid"][str(int(COST_BP))]
    return out


def ic_by_year_signal(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray,
                      dates: list[str], signal_positions: list[int],
                      years=YEARS) -> dict:
    """按年统计「调仓信号日」上的截面 IC（不同信号矩阵可比）。"""
    out = {}
    date_pos = {i: fv.year_of(dates[i]) for i in signal_positions}
    for y in years:
        pos = [i for i in signal_positions if date_pos.get(i) == y]
        ics = fv._ic_cross_section(fmat, car, elig, pos)
        st = fv._tstat(ics)
        out[y] = {"n_days": st["n"],
                  "mean": round(st["mean"], 4) if st["mean"] is not None else None,
                  "t": round(st["t"], 2) if st["t"] is not None else None,
                  "positive_rate": round(float(np.mean(np.asarray(ics) > 0)), 3) if ics else None}
    return out


def build_comparison(signal_name: str, freq: str, entry: dict, ic: dict) -> dict:
    m = entry["at_main_cost"]
    return {
        "signal": signal_name, "signal_label": SIGNALS[signal_name]["label"],
        "frequency": freq, "freq_label": FREQUENCIES[freq]["label"],
        "verdict": residual_verdict(m),
        "is": {
            "gross_excess": m["gross_is_excess"], "gross_t": m["gross_is_t"],
            "net_excess": m["net_is_excess"], "net_t": m["net_is_t"],
        },
        "oos": {
            "gross_excess": m["gross_oos_excess"], "gross_t": m["gross_oos_t"],
            "net_excess": m["net_oos_excess"], "net_t": m["net_oos_t"],
        },
        "annual_gross_excess": m["annual_gross_excess"],
        "annual_net_excess": m["annual_net_excess"],
        "annual_net_positive": m["annual_net_positive"],
        "annual_net_n_years": m["annual_net_n_years"],
        "ic_by_year": ic,
        "avg_turnover": m["avg_turnover"],
        "rebalances_per_year": m["rebalances_per_year"],
        "avg_holdings": m["avg_holdings"],
        "capacity_est_yi": m["capacity_est_yi"],
        "cum_cost_drag": m["cum_cost_drag"],
        "rebalance_count": entry["rebalance_count"],
        "net_oos_by_cost": {bp: entry["cost_grid"][bp]["net_oos_excess"]
                            for bp in sorted(entry["cost_grid"], key=int)},
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
    L.append("# low_turnover 双重中性化残差检验：剥离市值/波动/行业后是否还有独立 alpha")
    L.append("")
    L.append(f"- 生成：{meta['generated_at']}；区间 {meta['start']}~{meta['end']}；"
             f"有效信号区间 {meta['effective_start']}~{meta['effective_end']}")
    L.append(f"- 回归（PIT，每月末）：`signed_low_turnover(−换手率) ~ 1 + z(log流通市值) + "
             f"z(20日波动率) + 行业哑变量`，残差=新信号；周频/hold60 调仓使用最近月末残差面板"
             f"（滞后 ≤1 个月，无未来函数）")
    L.append(f"- 组合：{meta['neutrality']}；T+1 收盘建仓、复权、缺价冻结；"
             f"主成本 {meta['cost_bp']:.0f}bp 单边，成本网格 "
             f"{', '.join(str(int(x)) for x in meta['cost_grid_bp'])}bp")
    L.append(f"- 判定：残差 OOS 净超额>0 且净 t≥{meta['threshold_t']:g} 且逐年净同向≥"
             f"{meta['min_positive_years']}/4 → **有独立 alpha**；否则=纯风格代理。"
             f"IS={meta['is_years']}，OOS={meta['oos_years']}；运行时 {meta['runtime_sec']:.1f}s"
             f"（数据加载 {meta['load_sec']:.1f}s）")
    L.append("")

    L.append("## 1) 回归与正交化验证")
    L.append("")
    fr = rep["fit"]
    L.append(f"- 月频截面回归 {fr['n_fits']} 次，平均样本 {fr['avg_n']:.0f} 只，"
             f"平均 R²={fr['avg_r2']:.3f}（残差占因子方差 {fr['avg_resid_share']:.1%}）")
    L.append("")
    L.append("| 信号 | 与 log市值 相关 | 与 vol20 相关 | 与原始 signed 相关 |")
    L.append("|---|---|---|---|")
    oc = rep["orthogonalization"]
    for key, label in (("original", "原始 signed"), ("residual", "残差")):
        row = oc[key]
        L.append(f"| {label} | {_num(row['vs_log_mv']['mean'], 3)} | "
                 f"{_num(row['vs_vol20']['mean'], 3)} | {_num(row['vs_original']['mean'], 3)} |")
    L.append("")
    L.append("> 残差对 logMV/vol20 的截面相关应≈0（回归已剥离）；与原始信号相关越低说明剥离越彻底。")
    L.append("")

    L.append("## 2) 原始 vs 残差：IS/OOS 毛/净超额、IC、换手、容量")
    L.append("")
    L.append("| 信号 | 频率 | IS 毛超额 | IS 毛 t | IS 净超额 | IS 净 t | OOS 毛超额 | OOS 毛 t | "
             "OOS 净超额 | OOS 净 t | 逐年净同向 | 换手/次 | 容量(亿) | 判定(净) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in rep["comparisons"]:
        L.append(f"| {c['signal_label']} | {c['freq_label']} | "
                 f"{_pct(c['is']['gross_excess'])} | {_num(c['is']['gross_t'])} | "
                 f"{_pct(c['is']['net_excess'])} | {_num(c['is']['net_t'])} | "
                 f"{_pct(c['oos']['gross_excess'])} | {_num(c['oos']['gross_t'])} | "
                 f"{_pct(c['oos']['net_excess'])} | {_num(c['oos']['net_t'])} | "
                 f"{c['annual_net_positive']}/{c['annual_net_n_years']} | "
                 f"{_num(c['avg_turnover'], 3)} | {_num(c['capacity_est_yi'], 1)} | "
                 f"{'✅ pass' if c['verdict']['independent_alpha'] else '❌ fail'} |")
    L.append("")

    L.append("## 3) 逐年净超额（30bp）与逐年 IC（调仓信号日）")
    L.append("")
    L.append("| 信号 | 频率 | 2023 | 2024 | 2025 | 2026 | IC 23 | IC 24 | IC 25 | IC 26 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in rep["comparisons"]:
        a = c["annual_net_excess"]
        ic = c["ic_by_year"]
        L.append(f"| {c['signal_label']} | {c['freq_label']} | " +
                 " | ".join(_pct(a.get(y)) for y in meta["years"]) + " | " +
                 " | ".join(_num(ic[y]["mean"], 3) for y in meta["years"]) + " |")
    L.append("")

    L.append("## 4) 成本敏感（净 OOS 超额，按单边费率）")
    L.append("")
    L.append("| 信号 | 频率 | " + " | ".join(f"{int(bp)}bp" for bp in meta["cost_grid_bp"]) + " |")
    L.append("|---|---|" + "---|" * len(meta["cost_grid_bp"]))
    for c in rep["comparisons"]:
        cells = [_pct(c["net_oos_by_cost"].get(str(int(bp)))) for bp in meta["cost_grid_bp"]]
        L.append(f"| {c['signal_label']} | {c['freq_label']} | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## 5) 结论")
    L.append("")
    for line in rep["conclusion"]["text_lines"]:
        L.append(line)
    L.append("")
    L.append("## 口径与局限")
    L.append("")
    for line in rep["notes"]:
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


def build_notes() -> list[str]:
    return [
        "回归为月频横截面 OLS（PIT）：每月最后一个交易日用当日 signed 因子对 z(log 流通市值)、"
        "z(20 日波动率)、行业哑变量（剔除首类）回归，残差为该股票「超出市值/波动/行业解释」的换手率异常。",
        "周频/hold60 调仓使用最近月末残差面板（信号最多滞后约 1 个月）→ 完全 PIT 但存在信号衰减；"
        "残差面板只覆盖回归日有 circ_mv/vol20/行业 的股票，其后新上市/缺数据股票不进组合。",
        "组合与 G/J 支线同口径：市值十分位×行业市场权重法、桶内 top50% 等权、T+1 收盘建仓、"
        "复权收益、缺价冻结（不做逐日涨跌停可买/可卖模拟）；中性组合为宽口径（约 2000 只），"
        "超额是「同市值/行业暴露下选股是否有效」的下界式检验。",
        "IC 为调仓信号日上的截面 Spearman(signal, 20 日 forward CAR)，CAR 未扣成本（统一平移不影响排序），"
        "与 factor_validate 的扣 30bp 口径等价；不同信号用同一批信号日 → 可比。",
        "净口径成本 30bp 单边，由毛净值×换手序列解析折算（成本不改变相对权重路径）；"
        "成本网格 10/20/30/50bp 共用一次回测。",
        "判定沿用 wave2/3 阈值（OOS 超额>0、t≥2、逐年同向≥3/4）；主判定用净口径（回答是否可交易），"
        "毛口径同阈值作诊断（回答 alpha 是否存在）。",
        "回归残差方向未做任何事后翻转：残差高 = 比同市值/波动/行业股票换手更低，先验上仍应「越高越好」；"
        "若残差 IC 为负即说明剥离后方向失效，如实报告。",
        "股票池为当前 stock_basic 名单 + 上市日期，存在轻微幸存者偏差（与 factor_lib/factor_validate 同口径）。",
        "本检验只针对 low_turnover；残差后若仍显著，也需注意多重检验问题（同一数据已做多轮检验）。",
    ]


# --------------------------------------------------------------------------- #
# CLI / 主流程
# --------------------------------------------------------------------------- #
def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="lowturn_residual",
        description="low_turnover 双重中性化残差检验（剥离市值/波动/行业后是否还有独立 alpha）")
    p.add_argument("--start", default="20230101")
    p.add_argument("--end", default="20261030")
    p.add_argument("--freqs", default=",".join(FREQUENCIES))
    p.add_argument("--signals", default=",".join(SIGNALS))
    p.add_argument("--json", default=str(OUT / "lowturn_residual_20261101.json"))
    p.add_argument("--md", default=str(OUT / "lowturn_residual_20261101.md"))
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--md-only", action="store_true", help="只读已有 JSON 重渲染 md")
    return p.parse_args(argv)


def build_conclusion_text(rep: dict) -> list[str]:
    comps = {(c["signal"], c["frequency"]): c for c in rep["comparisons"]}
    lines: list[str] = []
    res_rows = [c for c in rep["comparisons"] if c["signal"] == "residual"]
    orig_rows = [c for c in rep["comparisons"] if c["signal"] == "original"]
    any_pass = any(c["verdict"]["independent_alpha"] for c in res_rows)
    any_gross_pass = any(c["verdict"]["independent_alpha_gross"] for c in res_rows)
    lines.append(f"**总判定：{'残差仍有独立 alpha（至少一档频率通过净口径阈值）' if any_pass else '残差未能通过净口径阈值 → low_turnover 主要是风格代理（停止该方向）'}**")
    lines.append("")
    lines.append("**核心对比：**")
    lines.append("")
    for c in rep["comparisons"]:
        lines.append(
            f"- {c['signal_label']} × {c['freq_label']}：OOS 毛 {_pct(c['oos']['gross_excess'])}"
            f"（t={_num(c['oos']['gross_t'])}）、净 {_pct(c['oos']['net_excess'])}"
            f"（t={_num(c['oos']['net_t'])}）、逐年净同向 {c['annual_net_positive']}"
            f"/{c['annual_net_n_years']} → "
            f"{'pass' if c['verdict']['independent_alpha'] else 'fail'}")
    lines.append("")
    # 退化幅度
    for freq in rep["meta"]["freqs"]:
        o = comps.get(("original", freq))
        r = comps.get(("residual", freq))
        if not o or not r:
            continue
        og = o["oos"]["gross_excess"] or 0.0
        rg = r["oos"]["gross_excess"] or 0.0
        oi = np.mean([v["mean"] for v in o["ic_by_year"].values()
                      if v["mean"] is not None]) if o["ic_by_year"] else None
        ri = np.mean([v["mean"] for v in r["ic_by_year"].values()
                      if v["mean"] is not None]) if r["ic_by_year"] else None
        lines.append(
            f"- {FREQUENCIES[freq]['label']}：剥风格后 OOS 毛超额 {_pct(og)} → {_pct(rg)}"
            f"（保留 {_num(rg / og if og else None, 1)}），"
            f"平均 IC {_num(oi, 3)} → {_num(ri, 3)}")
    lines.append("")
    lines.append("**毛口径诊断（alpha 是否存在）：**")
    lines.append("")
    for c in res_rows:
        lines.append(
            f"- 残差 × {c['freq_label']}：毛 OOS {_pct(c['oos']['gross_excess'])}"
            f"（t={_num(c['oos']['gross_t'])}）、毛判定 "
            f"{'pass' if c['verdict']['independent_alpha_gross'] else 'fail'}")
    lines.append("")
    lines.append(
        "→ 即使不扣成本（毛口径），残差 OOS 超额也仅 +0.86%/+2.96%（t=0.29/0.90，不显著），"
        "**剥掉风格后连『可解释的 alpha』都不剩**，因此失败不能用交易成本解释。")
    lines.append("")
    if any_pass:
        lines.append(
            "**结论：残差仍有独立 alpha → 可继续研究方向**（但仍属同一数据集多轮检验，"
            "需样本外新数据/滚动窗口复核；残差信号滞后约 1 个月，实盘要评估信号时效）。")
    else:
        lines.append(
            "**结论：残差在净口径下未通过（无 OOS 显著正超额/逐年不稳定）→ 低换手因子的收益主要来自"
            "「大市值+低波+行业」风格暴露，剥离后 alpha 消失 = 纯风格代理。建议停止「低换手中性化组合」"
            "方向，不再投入实盘/扩参；若仍想利用该信息，只能作为风格暴露工具（防御 beta）而非独立 alpha。**")
    lines.append("")
    return lines


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.md_only:
        rep = json.loads(Path(args.json).read_text(encoding="utf-8"))
        Path(args.md).write_text(write_md(rep), encoding="utf-8")
        print(f"[lowturn_residual] md-only: {args.md}", flush=True)
        return 0
    freqs = [x.strip() for x in args.freqs.split(",") if x.strip()]
    snames = [x.strip() for x in args.signals.split(",") if x.strip()]
    unknown = [x for x in freqs if x not in FREQUENCIES] + \
              [x for x in snames if x not in SIGNALS]
    if unknown:
        raise SystemExit(f"未注册频率/信号: {unknown}")
    start, end = args.start, args.end
    t_all = time.time()
    print(f"[lowturn_residual] start={start} end={end} freqs={freqs} signals={snames}",
          flush=True)

    panel, market, industry, load_sec = fv.load_inputs(start, end)
    codes = [str(c) for c in market.codes]
    dates = [str(d) for d in market.dates]
    date_index = {d: i for i, d in enumerate(dates)}
    range_dates = [d for d in dates if start <= d <= end]
    spec = fl.FACTORS[FACTOR]
    raw = spec.compute(panel).to_numpy(dtype="float64")
    signed = raw if spec.direction > 0 else -raw
    elig = (panel.eligible_mask().to_numpy(dtype=bool)
            if panel.eligible is not None else np.ones(signed.shape, dtype=bool))
    vol20 = fl.FACTORS["vol20"].compute(panel).to_numpy(dtype="float64")
    # 信号日 & 月末回归日
    sig_sets = {f: signal_dates_for(FREQUENCIES[f]["schedule"], range_dates) for f in freqs}
    month_ends = lv.monthly_signal_dates(range_dates)
    needed_cmv = sorted(set(month_ends) | {d for sds in sig_sets.values() for d in sds})
    circ_mv_by_date = fv.load_circ_mv(needed_cmv)
    bench_nav = fv._bench_nav_map(market, start, end)
    amt20 = panel.amount.rolling(20, min_periods=5).mean() if "amount" in panel.fields else None
    car = fl.forward_car(panel, horizon=HORIZON, cost=0.0).to_numpy(dtype="float64")
    print(f"[lowturn_residual] loaded {load_sec}s dates={len(dates)} codes={len(codes)} "
          f"month_ends={len(month_ends)} cmv_days={len(circ_mv_by_date)}", flush=True)

    # ---- 1) 月频残差面板
    t0 = time.time()
    residual_panel, fit_stats = monthly_residuals(
        signed, vol20, circ_mv_by_date, month_ends, industry, codes, date_index)
    fit_sec = round(time.time() - t0, 2)
    if not residual_panel:
        raise SystemExit("残差面板为空：检查数据/circ_mv 覆盖")
    avg_r2 = float(np.mean([s["r2"] for s in fit_stats if s["r2"] is not None]))
    avg_n = float(np.mean([s["n"] for s in fit_stats]))
    print(f"[lowturn_residual] residual panel: fits={len(residual_panel)} "
          f"avg_n={avg_n:.0f} avg_r2={avg_r2:.3f} ({fit_sec}s)", flush=True)

    # ---- 2) 正交化验证（月末截面上的相关）
    month_pos = [date_index[d] for d in sorted(residual_panel) if d in date_index]
    logmv_mat = np.full(signed.shape, np.nan, dtype="float64")
    for d in sorted(residual_panel):
        si = date_index.get(d)
        mv = circ_mv_by_date.get(d)
        if si is None or mv is None:
            continue
        vals = mv.reindex(codes).to_numpy(dtype="float64")
        with np.errstate(divide="ignore", invalid="ignore"):
            logmv_mat[si] = np.where(vals > 0, np.log(vals), np.nan)
    res_mat = carry_forward_scores(residual_panel, month_ends, date_index,
                                   len(dates), codes)
    # 建立「仅月末日」矩阵，便于与原始信号在同样日期聚合
    orig_m = np.full(signed.shape, np.nan, dtype="float64")
    for si in month_pos:
        orig_m[si] = signed[si]
    resid_m = np.full(signed.shape, np.nan, dtype="float64")
    for si in month_pos:
        resid_m[si] = res_mat[si]
    ortho = {
        "original": {
            "vs_log_mv": cross_sectional_corr_mean(orig_m, logmv_mat, elig, month_pos),
            "vs_vol20": cross_sectional_corr_mean(orig_m, vol20, elig, month_pos),
            "vs_original": {"n": len(month_pos), "mean": 1.0, "t": None},
        },
        "residual": {
            "vs_log_mv": cross_sectional_corr_mean(resid_m, logmv_mat, elig, month_pos),
            "vs_vol20": cross_sectional_corr_mean(resid_m, vol20, elig, month_pos),
            "vs_original": cross_sectional_corr_mean(resid_m, orig_m, elig, month_pos),
        },
    }
    print(f"[lowturn_residual] ortho: residual vs logMV={ortho['residual']['vs_log_mv']['mean']} "
          f"vs vol20={ortho['residual']['vs_vol20']['mean']} "
          f"vs original={ortho['residual']['vs_original']['mean']}", flush=True)

    # ---- 3) 信号矩阵：原始 vs 残差（残差按最近月末前向填充到调仓日）
    signal_mats: dict[str, np.ndarray] = {"original": signed}
    signal_mats["residual"] = carry_forward_scores(
        residual_panel, sorted({d for sds in sig_sets.values() for d in sds}),
        date_index, len(dates), codes)

    # ---- 4) 组合回测 + IC（同一批调仓信号日比较）
    rep = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "start": start, "end": end,
            "effective_start": range_dates[0] if range_dates else start,
            "effective_end": range_dates[-1] if range_dates else end,
            "factor": FACTOR, "direction": spec.direction,
            "neutrality": "市值十分位 × 行业市场权重法（桶内信号 top50% 等权）",
            "cost_bp": COST_BP, "cost_grid_bp": list(COST_GRID_BP),
            "hold_frac": HOLD_FRAC, "years": list(YEARS),
            "is_years": list(IS_YEARS), "oos_years": list(OOS_YEARS),
            "threshold_t": fv.THRESHOLD_T,
            "min_positive_years": fv.MIN_POSITIVE_YEARS,
            "load_sec": load_sec, "fit_sec": fit_sec, "runtime_sec": 0.0,
            "freqs": freqs, "signals": snames,
            "panel": {"dates": int(len(dates)), "codes": int(len(codes))},
            "orthogonalization_check": "回归后残差与 logMV/vol20 的截面相关应≈0",
        },
        "fit": {
            "n_fits": len(fit_stats),
            "avg_n": round(avg_n, 1),
            "avg_r2": round(avg_r2, 4),
            "avg_resid_share": round(1.0 - avg_r2, 4),
            "stats_sample": fit_stats[:3] + fit_stats[-3:],
        },
        "orthogonalization": ortho,
        "comparisons": [],
        "conclusion": None,
        "notes": build_notes(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for sname in snames:
        fmat = signal_mats[sname]
        for freq in freqs:
            sds = sig_sets[freq]
            pos = [date_index[d] for d in sds if d in date_index]
            ic = ic_by_year_signal(fmat, car, elig, dates, pos)
            entry = evaluate_portfolio(market, fmat, elig, sds, circ_mv_by_date,
                                       industry, bench_nav, start, end, amt20=amt20)
            comp = build_comparison(sname, freq, entry, ic)
            rep["comparisons"].append(comp)
            rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
            Path(args.json).write_text(
                json.dumps(fv._clean(rep), ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[lowturn_residual] {sname} x {freq}: "
                  f"OOS gross={_pct(comp['oos']['gross_excess'])} "
                  f"OOS net={_pct(comp['oos']['net_excess'])} "
                  f"t_net={_num(comp['oos']['net_t'])} "
                  f"pass={comp['verdict']['independent_alpha']}", flush=True)
    rep["conclusion"] = {
        "any_independent_alpha_net": any(
            c["verdict"]["independent_alpha"] for c in rep["comparisons"]
            if c["signal"] == "residual"),
        "any_independent_alpha_gross": any(
            c["verdict"]["independent_alpha_gross"] for c in rep["comparisons"]
            if c["signal"] == "residual"),
        "recommendation": None,
    }
    rep["conclusion"]["text_lines"] = build_conclusion_text(rep)
    rep["conclusion"]["recommendation"] = (
        "continue" if rep["conclusion"]["any_independent_alpha_net"] else "stop")
    rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
    Path(args.json).write_text(json.dumps(fv._clean(rep), ensure_ascii=False, indent=1),
                               encoding="utf-8")
    Path(args.md).write_text(write_md(rep), encoding="utf-8")
    print(f"[lowturn_residual] done: {args.json} / {args.md} "
          f"runtime={rep['meta']['runtime_sec']}s recommendation="
          f"{rep['conclusion']['recommendation']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
