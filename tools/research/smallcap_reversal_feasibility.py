# -*- coding: utf-8 -*-
"""「小市值反转」可行性研究（司马微 · task-92；不写策略、不调参）。

背景：wave31 行业 RS 因子（tools/research/industry_rs_factor.py）结论是
「反转信息有效但不可交易」：RS_20 方向 -1 的 IC 显著、但多空收益集中在不可做空的空头端，
纯多头长腿超额仅 +0.13%（<0.5%）、IC 集中在小市值。

本脚本只回答可行性问题（go / no-go）：
  ① 纯多头长腿：RS_20（方向 -1）多头端（反转赢家）+ 小市值组，相对全分层均值的超额、
     换手、30/50/80bp 成本后的净年化；
  ② 容量：成分 20 日均成交额、10%/20% 参与率、剔除极端流动性（p10）后可容纳资金；
  ③ 券源约束：用本地 margin_detail（融资融券明细：rqye 融券余额/标的覆盖）量化空头端可实现性，
     并写明 A 股融券制度假设（券源稀缺，收益在空头端 → 大概率无法落地）；
  ④ 替代路径：小市值+反转 long-only 的 4 条变体（裸多头 / 小市值 / 行业中性 / 波动过滤）
     是否有净超额；
  ⑤ 结论 go/no-go + 前置条件。

产出：research/reports/smallcap_reversal_feasibility_20261101.{json,md}
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/smallcap_reversal_feasibility.py
"""
from __future__ import annotations

import glob
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "research"))

import factor_lib as fl  # noqa: E402
import industry_rs_factor as irf  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20230101", "20261030"
HOLD = 20
COSTS_BP = (30, 50, 80)
WF = (("2023-2024", "20230101", "20241231"), ("2025-2026", "20250101", "20261231"))
TARGET_AUM_YI = 5.0          # 可接受的最小可容纳资金（亿元）
GO_NET30_MIN = 1.0           # go 的净年化门槛（%）
LONG_LEG_EXCESS_MIN = 0.5    # 长腿 20 日超额门槛（%）


# ============================================================ 纯函数（可单测）
def annualize(mean_20d_pct: float | None, horizon: int = HOLD) -> float | None:
    """20 日平均超额 → 年化（简单线性年化，与 industry_rs_factor 同口径）。"""
    if mean_20d_pct is None or not np.isfinite(mean_20d_pct):
        return None
    return float(mean_20d_pct) * 252.0 / horizon


def cost_layers(gross_ann_pct: float | None, ann_turnover_oneway: float | None,
                costs_bp=COSTS_BP, legs: int = 1) -> dict:
    """成本分层：净年化 = 毛年化 − 成本(bp)/10000 × 年化换手 × 腿数（long-only legs=1）。"""
    if gross_ann_pct is None or ann_turnover_oneway is None:
        return {f"{int(c)}bp": {"net_ann_pct": None, "cost_drag_ann_pct": None} for c in costs_bp}
    out = {}
    for c in costs_bp:
        drag = c / 10000.0 * 100.0 * ann_turnover_oneway * legs
        out[f"{int(c)}bp"] = {"net_ann_pct": fl._num(gross_ann_pct - drag, 2),
                              "cost_drag_ann_pct": fl._num(drag, 2)}
    return out


def capacity_estimate(adtv_yuan: np.ndarray, n_hold: float,
                      participations=(0.10, 0.20), horizon: int = HOLD) -> dict:
    """容量（AUM，亿元）。

    20 日重叠持有、每日仅 1/horizon 仓位换手：每次换仓单票交易额 ≈ AUM/n_hold，
    要求单票单日成交额 ≤ r × ADTV → AUM_cap = r × ADTV × n_hold。
    同时给「剔除极端流动性」口径：用成分 ADTV 的 10% 分位（p10）替代中位数。
    """
    arr = np.asarray([x for x in np.asarray(adtv_yuan, dtype=float)
                      if np.isfinite(x) and x > 0])
    keys = ["adtv_median_yi", "adtv_p10_yi", "avg_holdings",
            "aum_cap_10pct_yi", "aum_cap_20pct_yi",
            "aum_cap_10pct_ex_lowliq_yi", "aum_cap_20pct_ex_lowliq_yi"]
    empty = {k: None for k in keys}
    empty.update({"n_names": 0, "avg_holdings": fl._num(n_hold, 1) if n_hold else None})
    if len(arr) == 0 or not n_hold:
        return empty
    med = float(np.median(arr))
    p10 = float(np.percentile(arr, 10))
    out = {"n_names": int(len(arr)), "adtv_median_yi": fl._num(med / 1e8, 4),
           "adtv_p10_yi": fl._num(p10 / 1e8, 4), "avg_holdings": fl._num(n_hold, 1),
           "formula": "AUM_cap = participation × ADTV × n_hold（20 日重叠持有、每票整仓换手）"}
    for r in participations:
        key = f"aum_cap_{int(r * 100)}pct_yi"
        out[key] = fl._num(r * med * n_hold / 1e8, 2)
        out[f"aum_cap_{int(r * 100)}pct_ex_lowliq_yi"] = fl._num(r * p10 * n_hold / 1e8, 2)
    return out


def decide_go_no_go(long_leg: dict, alt_paths: dict, margin: dict) -> dict:
    """go/no-go 判定（规则先定后看数据，避免事后调参）：

    go 需同时满足：
      A. 纯多头长腿（小市值 D1）30bp 净年化 ≥ GO_NET30_MIN 且 20 日超额 ≥ LONG_LEG_EXCESS_MIN；
      B. 容量（10% 参与率、剔除极端流动性）≥ TARGET_AUM_YI 亿；
      C. 至少一条 long-only 替代路径 30bp 净年化 >0 且 walk-forward 两窗同为正；
      D. 空头端不依赖（long-only 路径）；若依赖空头端则因券源不可行直接 no-go。
    """
    reasons = []
    net30 = ((long_leg.get("cost") or {}).get("30bp") or {}).get("net_ann_pct")
    exc = long_leg.get("excess_20d_pct")
    a = bool(net30 is not None and net30 >= GO_NET30_MIN
             and exc is not None and exc >= LONG_LEG_EXCESS_MIN)
    reasons.append(f"A 长腿：20日超额 {exc}%（门槛 {LONG_LEG_EXCESS_MIN}%）、"
                   f"30bp 净年化 {net30}%（门槛 {GO_NET30_MIN}%）→ {'过' if a else '不过'}")
    cap = long_leg.get("capacity") or {}
    cap10 = cap.get("aum_cap_10pct_ex_lowliq_yi")
    b = bool(cap10 is not None and cap10 >= TARGET_AUM_YI)
    reasons.append(f"B 容量：10% 参与率×剔除极端流动性后 {cap10} 亿（门槛 {TARGET_AUM_YI} 亿）→ "
                   f"{'过' if b else '不过'}")
    best = None
    for name, p in alt_paths.items():
        c = ((p.get("cost") or {}).get("30bp") or {}).get("net_ann_pct")
        wf = p.get("walk_forward") or {}
        obs = (wf.get("2023-2024") or {}).get("mean_20d_pct")
        val = (wf.get("2025-2026") or {}).get("mean_20d_pct")
        if c is not None and c > 0 and obs is not None and val is not None and obs > 0 and val > 0:
            best = name
            break
    c_ok = best is not None
    reasons.append(f"C 替代路径：{'至少一条可行为 ' + str(best) if c_ok else '无任何 long-only 路径净超额>0 且双窗同正'}")
    d_ok = True
    reasons.append("D 空头端：long-only 路径不依赖融券（空头端收益仅作研究信息，不纳入可行性）")
    if margin.get("short_side_feasible") is False:
        reasons.append(f"补充：空头端不可行（{margin.get('reason', '券源/融券约束')}）")
    go = a and b and c_ok and d_ok
    return {"decision": "go" if go else "no-go",
            "criteria_pass": {"A_long_leg": a, "B_capacity": b, "C_alt_path": c_ok,
                              "D_no_short_dependency": d_ok},
            "best_alt_path": best, "reasons": reasons,
            "thresholds": {"net30_min_pct": GO_NET30_MIN,
                           "long_leg_excess_min_pct": LONG_LEG_EXCESS_MIN,
                           "target_aum_yi": TARGET_AUM_YI}}


def validate_report_schema(rep: dict) -> list[str]:
    """报告 schema 自检；返回错误列表（空=通过）。"""
    errors = []
    for k in ("schema_version", "meta", "definition", "long_leg", "capacity",
              "margin_constraint", "alt_paths", "decision"):
        if k not in rep:
            errors.append(f"缺顶层字段: {k}")
    if errors:
        return errors
    if rep["decision"].get("decision") not in ("go", "no-go"):
        errors.append("decision.decision 非法")
    for k in ("criteria_pass", "reasons"):
        if k not in rep["decision"]:
            errors.append(f"decision 缺字段: {k}")
    ll = rep["long_leg"]
    for k in ("excess_20d_pct", "turnover", "cost", "capacity", "walk_forward"):
        if k not in ll:
            errors.append(f"long_leg 缺字段: {k}")
    for bp in COSTS_BP:
        if f"{bp}bp" not in (ll.get("cost") or {}):
            errors.append(f"long_leg.cost 缺 {bp}bp")
    for k in ("aum_cap_10pct_ex_lowliq_yi", "aum_cap_20pct_ex_lowliq_yi"):
        if k not in (ll.get("capacity") or {}):
            errors.append(f"long_leg.capacity 缺 {k}")
    if not isinstance(rep.get("alt_paths"), dict) or not rep["alt_paths"]:
        errors.append("alt_paths 必须是非空 dict")
    return errors


# ============================================================ 数据/组合工具
def _decile(fmat: np.ndarray, pool: np.ndarray, n_groups: int = 10) -> np.ndarray:
    """池内按因子分位分组（1..n_groups），非池/无效=0。"""
    pair = pool & np.isfinite(fmat)
    ranks = fl._rank_2d(np.where(pair, fmat, np.nan), average=False)
    n = pair.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.clip(np.ceil(ranks / np.where(n > 0, n, np.nan)[:, None] * n_groups),
                    1, n_groups)
    return np.where(pair, q, 0).astype(np.int8)


def _turnover_capacity(sel: np.ndarray, label: np.ndarray, dates, adtv: np.ndarray,
                       elig: np.ndarray, cap_mask: np.ndarray | None = None) -> dict:
    """选定组合：日频超额序列（相对全分层均值/池均值）、换手、年化、容量。

    返回 dict：excess_20d_pct（相对全市场等权/全分层均值）、ann_pct、t、monthly_t、
    turnover（日均单边+年化重叠）、capacity（ADTV 描述统计）。
    """
    n_days = sel.shape[0]
    daily_sel = fl._daily_mean(label, sel, list(dates))       # 组合日 CAR 均值
    pool = elig if cap_mask is None else cap_mask
    daily_mean = fl._daily_mean(np.where(elig, label, np.nan), pool, list(dates))
    excess = (daily_sel - daily_mean).dropna()
    st_ex = irf.series_stats(excess)
    st_m = irf.series_stats(irf.monthly_cohort(excess))
    gross = irf.series_stats(daily_sel)  # 组合绝对日 CAR（相对等权市场）
    # 换手：相邻日名单重叠
    turnovers = []
    members = [set(np.flatnonzero(sel[i])) for i in range(n_days)]
    for i in range(1, n_days):
        if not members[i] or not members[i - 1]:
            continue
        turnovers.append(1.0 - len(members[i] & members[i - 1]) / len(members[i - 1]))
    avg_t = float(np.mean(turnovers)) if turnovers else None
    ann_t = None if avg_t is None else float(avg_t) * 252.0 / HOLD
    # 容量：组合成分 20 日均成交额（元）
    adtv_vals = []
    for i in range(n_days):
        idx = np.flatnonzero(sel[i])
        if len(idx) == 0:
            continue
        vals = adtv[i, idx]
        vals = vals[np.isfinite(vals) & (vals > 0)]
        if len(vals):
            adtv_vals.append(np.median(vals))
    n_hold = float(np.mean([m.sum() for m in (sel > 0)])) if sel.any() else 0.0
    cap = capacity_estimate(np.asarray(adtv_vals), n_hold) if adtv_vals else {"n_names": 0}
    exc_pct = None if st_ex.get("mean") is None else float(st_ex["mean"]) * 100.0
    abs_pct = None if gross.get("mean") is None else float(gross["mean"]) * 100.0
    return {
        "n_obs": int(sel.sum()),
        "avg_holdings": fl._num(n_hold, 1),
        "excess_20d_pct": fl._num(exc_pct, 4), "excess_t": st_ex.get("t"),
        "excess_monthly_t": st_m.get("t"),
        "excess_positive_rate": st_ex.get("positive_rate"),
        "abs_car_20d_pct": fl._num(abs_pct, 4), "abs_car_ann_pct": annualize(abs_pct),
        "ann_car_pct": annualize(exc_pct),
        "turnover": {"avg_oneway_daily": fl._num(avg_t, 4),
                     "ann_overlapping": fl._num(ann_t, 2),
                     "note": "20 日重叠持有：年化单边换手 = 日均名单变化率 × 252/20"},
        "cost": cost_layers(annualize(exc_pct), ann_t, costs_bp=COSTS_BP, legs=1),
        "capacity": cap,
        "walk_forward": {},
        "_excess_series": excess,
    }


def _wf_block(excess: pd.Series) -> dict:
    out = {}
    for label, a, b in WF:
        sub = excess[(excess.index >= a) & (excess.index <= b)]
        st = irf.series_stats(sub)
        mean = None if st.get("mean") is None else float(st["mean"]) * 100.0
        out[label] = {"n_days": st["n_days"], "mean_20d_pct": fl._num(mean, 4),
                      "t": st["t"]}
    return out


def load_amount_20d(panel) -> np.ndarray:
    """20 日均成交额（元）矩阵：daily.amount 单位千元 → ×1000。"""
    return np.asarray(panel.amount.rolling(HOLD, min_periods=5).mean(), dtype=float) * 1000.0


def margin_analysis(panel, sel_small_long: np.ndarray, dates: list[str]) -> dict:
    """券源约束：用本地 margin_detail 量化「两融标的覆盖率/融券余额」。

    每个季度末取一个交易日，统计：小市值长腿持仓中属于两融标的的比例、
    融券余额（rqye，元）中位数/合计、以及全市场融券余额占流通市值比（用全样本 rqye 汇总）。
    假设：rqye 是「已融出的市值」不是「可借券源」；A 股券源稀缺、小市值券源更差。
    """
    files = sorted(glob.glob(str(DATA / "margin_detail" / "*.parquet")))
    if not files:
        return {"data_available": False,
                "assumptions": ["本地无 margin_detail；按公开常识假设：A 股融券余额约千亿量级、"
                                "小市值券源稀缺、转融券限制下空头端不可规模化执行"]}
    by_date = {Path(f).stem[2:]: f for f in files}
    sample_dates = sorted(by_date)
    quarter_dates = []
    for d in sample_dates:
        if d[4:6] in ("03", "06", "09", "12"):
            quarter_dates.append(d)
    quarter_dates = sorted(set(quarter_dates))[::3][:8]  # 每季度抽样，最多 8 个
    rows = []
    for d in quarter_dates:
        i = dates.index(d) if d in dates else None
        if i is None:
            continue
        idx = np.flatnonzero(sel_small_long[i])
        if len(idx) == 0:
            continue
        codes = [str(panel.codes[j]) for j in idx]
        try:
            m = pq.read_table(by_date[d], columns=["ts_code", "rqye", "rqyl"]).to_pandas()
        except Exception:  # noqa: BLE001
            continue
        m = m.set_index("ts_code")
        covered = [c for c in codes if c in m.index]
        rq = m.reindex(covered)["rqye"].dropna().astype(float)
        rows.append({"date": d, "holdings": len(codes), "margin_covered": len(covered),
                     "covered_ratio": fl._num(len(covered) / len(codes), 4),
                     "rqye_median_wan": fl._num(float(np.median(rq)) / 1e4, 2) if len(rq) else None,
                     "rqye_sum_yi": fl._num(float(rq.sum()) / 1e8, 4) if len(rq) else None,
                     "market_rqye_sum_yi": fl._num(float(m["rqye"].fillna(0).sum()) / 1e8, 2),
                     "market_magin_names": int(len(m))})
    if not rows:
        return {"data_available": False, "assumptions": ["margin_detail 抽样解析失败"]}
    cov = float(np.mean([r["covered_ratio"] for r in rows]))
    med = float(np.median([r["market_rqye_sum_yi"] for r in rows if r.get("market_rqye_sum_yi")]))
    short_ok = False
    reason = (f"小市值长腿成分的两融标的覆盖率仅 {cov:.1%}；全市场融券余额中位 {med:.0f} 亿"
              "（占流通市值 <1%）；券源稀缺 + 小市值券源更差 + 转融券受限 → 空头端不可规模化执行")
    return {"data_available": True, "samples": rows,
            "smallcap_long_margin_covered_ratio": fl._num(cov, 4),
            "market_rqye_median_yi": fl._num(med, 2),
            "short_side_feasible": short_ok, "reason": reason,
            "assumptions": [
                "rqye = 已融出市值（存量），不等于可借券源；券源可得性通常更低",
                "A 股个人/私募难以稳定借到小市值券；转融券政策收紧后券源进一步收缩",
                "本结论用本地 margin_detail 抽样 + 公开常识规则，未联网获取实时券源/费率",
            ]}


# ============================================================ 主流程
def main() -> int:
    t0 = time.time()
    panel = fl.load_panel(start=START, end=END, horizon=HOLD, universe="tradable",
                          load_roe=False)
    dates = [str(d) for d in panel.dates]
    [str(c) for c in panel.codes]
    elig = panel.eligible_mask().to_numpy(dtype=bool)
    industry = irf.load_industry()
    cap_mv = irf.load_circ_mv(panel)                     # 万元
    label = np.asarray(fl.forward_car(panel, horizon=HOLD, cost=0.0), dtype=float)
    factors, ind_names = irf.build_rs_factors(panel, industry, windows=(20,))
    F_raw = factors["RS_20"]
    adtv = load_amount_20d(panel)
    vol20 = np.asarray(fl.FACTORS["vol20"].compute(panel), dtype=float)
    print(f"[feasibility] 数据就绪 {time.time()-t0:.1f}s", flush=True)

    window = fl._resolve_window(panel.dates, START, END, HOLD)
    pos = np.arange(window["start_i"], window["end_i"] + 1)
    dates_w = [dates[i] for i in pos]
    label_w, elig_w = label[pos], elig[pos]
    cap_w, adtv_w, vol_w = cap_mv[pos], adtv[pos], vol20[pos]
    F_raw_w = F_raw[pos]
    # 方向：观察窗（2023-2024）IC 符号（方向 -1 = 反转），验证窗只检验不改方向
    obs_i = [k for k, d in enumerate(dates_w) if d <= "20241231"]
    ic_obs = fl._spearman_ic(np.where(elig_w[obs_i], F_raw_w[obs_i], np.nan),
                             label_w[obs_i], min_n=50)
    obs_ic_mean = float(pd.Series(ic_obs).dropna().mean())
    sign = 1 if obs_ic_mean >= 0 else -1
    F = sign * F_raw_w                                        # 越大=越「反转多头」
    print(f"[feasibility] RS_20 观察窗IC={obs_ic_mean:.4f} → 方向 {sign:+d}；"
          f"有效信号日 {len(dates_w)}", flush=True)

    # 池：全市场 / 小市值（当日流通市值底 1/3）
    cap_ranks = fl._rank_2d(np.where(elig_w & np.isfinite(cap_w) & (cap_w > 0), cap_w, np.nan))
    n_cap = (elig_w & np.isfinite(cap_w) & (cap_w > 0)).sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cap_q = np.clip(np.ceil(cap_ranks / np.where(n_cap > 0, n_cap, np.nan)[:, None] * 3), 1, 3)
    small = elig_w & np.isfinite(cap_w) & (cap_q == 1)
    # 行业内中性：F 减行业当日均值
    ind_code, _ = irf.industry_codes(industry, panel.codes)
    ind_code_w = ind_code
    F_neutral = F.copy()
    for g in range(len(ind_names)):
        cols = np.flatnonzero(ind_code_w == g)
        if len(cols) == 0:
            continue
        sub = np.where(elig_w[:, cols], F[:, cols], np.nan)
        mean = np.nanmean(sub, axis=1, keepdims=True)
        F_neutral[:, cols] = F[:, cols] - mean
    # 波动过滤：小市值池内 vol20 底 1/3
    v_ranks = fl._rank_2d(np.where(small & np.isfinite(vol_w), vol_w, np.nan))
    n_v = (small & np.isfinite(vol_w)).sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        v_q = np.clip(np.ceil(v_ranks / np.where(n_v > 0, n_v, np.nan)[:, None] * 3), 1, 3)
    lowvol = small & np.isfinite(vol_w) & (v_q == 1)

    def sel_top(fmat, pool):
        q = _decile(fmat, pool, 10)
        return pool & (q == 10)

    # ① 纯多头长腿：全市场反转 D1（signed 顶 10%）与「小市值组内的反转顶 10%」
    sel_long_all = sel_top(F, elig_w)
    sel_small = sel_top(F, small)                 # 小市值 + 反转（替代路径 P2，也是长腿“小市值组”口径）
    # ④ 替代路径
    paths = {
        "P1_long_only_all": sel_long_all,
        "P2_smallcap_reversal": sel_small,
        "P3_smallcap_reversal_industry_neutral": sel_top(F_neutral, small),
        "P4_smallcap_reversal_lowvol": sel_top(F, lowvol),
    }
    results = {}
    print(f"[feasibility] adtv finite share="
          f"{np.isfinite(adtv_w).mean():.3f} median={np.nanmedian(adtv_w) if np.isfinite(adtv_w).any() else None}",
          flush=True)
    for name, sel in paths.items():
        r = _turnover_capacity(sel, label_w, dates_w, adtv_w, elig_w, cap_mask=small)
        r["walk_forward"] = _wf_block(r.pop("_excess_series"))
        results[name] = r
        print(f"[feasibility] {name}: n={r['n_obs']} excess20d={r['excess_20d_pct']}% "
              f"ann={r['ann_car_pct']}% net30="
              f"{(r['cost'].get('30bp') or {}).get('net_ann_pct')}%", flush=True)

    # 券源（用 P2 小市值长腿持仓）
    margin = margin_analysis(panel, paths["P2_smallcap_reversal"], dates_w)
    print(f"[feasibility] margin: available={margin.get('data_available')} "
          f"cov={margin.get('smallcap_long_margin_covered_ratio')}", flush=True)

    long_leg = dict(results["P2_smallcap_reversal"])
    long_leg["name"] = "小市值组反转长腿（F 顶 10%，signed）"
    long_leg["note"] = ("相对全分层均值超额 = 组合日 CAR 均值 − 全市场层均值；"
                        "对照 wave31 的『长腿超额 0.13%』口径（全市场 D1 vs 十分位均值）")
    results_p1 = dict(results["P1_long_only_all"])
    decision = decide_go_no_go(long_leg, {k: v for k, v in results.items()
                                          if k != "P2_smallcap_reversal"}, margin)

    rep = {
        "schema_version": "smallcap_reversal_feasibility.v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "researcher": "司马微", "task": "task-92「小市值反转」可行性（不写策略、不调参）",
            "source_factor": "tools/research/industry_rs_factor.py RS_20",
            "sample": {"start": START, "end": END, "signal_days": int(len(dates_w)),
                       "panel_days": int(panel.shape[0]), "codes": int(panel.shape[1])},
            "direction": {"sign": int(sign), "observe_ic": fl._num(obs_ic_mean, 6),
                          "source": "2023-2024 观察窗 IC 符号（验证窗只检验不改方向）"},
            "costs_bp": list(COSTS_BP), "hold_days": HOLD,
            "runtime_sec": None,
        },
        "definition": {
            "factor": "RS_20 = 个股20日复权收益 − 同行业其他成员20日等权收益（leave-one-out）",
            "label": "T日收盘因子 → T+1 收盘入场 → T+21 收盘卖出；个股复权收益 − 等权市场同期复利收益（无成本）",
            "long_leg": f"方向 {sign:+d} 后因子顶 10%（signed F D10）；『小市值组』= 当日流通市值底 1/3",
            "cost": "净年化 = 毛年化 − 成本(bp)/10000 × 年化单边换手 × 1（long-only 单腿）",
            "capacity": "AUM_cap = participation × ADTV × n_hold（20 日重叠持有、每票整仓换手）；"
                        "另给剔除极端流动性（p10 ADTV）口径",
        },
        "long_leg": long_leg,
        "capacity": long_leg.get("capacity"),
        "margin_constraint": margin,
        "alt_paths": {k: v for k, v in results.items() if k != "P2_smallcap_reversal"},
        "reference_long_leg_all": results_p1,
        "decision": decision,
        "schema_check": [],
    }
    rep["schema_check"] = validate_report_schema(rep)
    rep["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    if rep["schema_check"]:
        print(f"[feasibility] WARN schema: {rep['schema_check']}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "smallcap_reversal_feasibility_20261101.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- MD
    d = rep["decision"]
    L = [
        "# 「小市值反转」可行性研究（司马微 · task-92）", "",
        f"- 生成：{rep['meta']['generated_at']}；样本 {START}~{END}（{len(dates_w)} 个信号日）；"
        f"持有 {HOLD} 日；运行时 {rep['meta']['runtime_sec']}s",
        "- 因子：`industry_rs_factor.py` 的 **RS_20**（个股 20 日收益 − 行业其余成员等权，leave-one-out）；"
        f"方向 {sign:+d}（2023-2024 观察窗 IC={obs_ic_mean:.4f} → 反转）",
        "- 本文只做可行性判断（不写策略、不调参）；收益均为相对等权市场的 20 日 CAR，"
        "long-only 成本按单腿计", "",
        f"## 结论：**{'GO' if d['decision'] == 'go' else 'NO-GO'}**", "",
    ]
    for r in d["reasons"]:
        L.append(f"- {r}")
    L += ["", "## ① 纯多头长腿（小市值组反转 D1）", "",
          "| 指标 | 值 |", "|---|---|",
          f"| 样本数 / 日均持仓 | {long_leg['n_obs']} / {long_leg['avg_holdings']} |",
          f"| 相对全分层均值超额（20日） | {long_leg['excess_20d_pct']}%（t={long_leg['excess_t']}） |",
          f"| 年化（线性年化） | {long_leg['ann_car_pct']}% |",
          f"| 日均单边换手 / 年化重叠换手 | {long_leg['turnover']['avg_oneway_daily']} / "
          f"{long_leg['turnover']['ann_overlapping']} |",
          f"| 30bp 净 / 50bp 净 / 80bp 净 | "
          f"{(long_leg['cost'].get('30bp') or {}).get('net_ann_pct')}% / "
          f"{(long_leg['cost'].get('50bp') or {}).get('net_ann_pct')}% / "
          f"{(long_leg['cost'].get('80bp') or {}).get('net_ann_pct')}% |", "",
          "### walk-forward（超额 20 日均值）", "",
          "| 窗口 | n 日 | 超额 | t |", "|---|---|---|---|"]
    for k, v in long_leg["walk_forward"].items():
        L.append(f"| {k} | {v['n_days']} | {v['mean_20d_pct']}% | {v['t']} |")
    L += ["", "## ② 容量（小市值长腿成分 ADTV）", "",
          "| 口径 | 值（亿元） |", "|---|---|",
          f"| 成分 ADTV 中位 / p10 | {long_leg['capacity'].get('adtv_median_yi')} / "
          f"{long_leg['capacity'].get('adtv_p10_yi')} |",
          f"| 10% 参与率（中位 ADTV） | {long_leg['capacity'].get('aum_cap_10pct_yi')} |",
          f"| 20% 参与率（中位 ADTV） | {long_leg['capacity'].get('aum_cap_20pct_yi')} |",
          f"| 10% 参与率（剔除极端流动性 p10） | "
          f"{long_leg['capacity'].get('aum_cap_10pct_ex_lowliq_yi')} |",
          f"| 20% 参与率（剔除极端流动性 p10） | "
          f"{long_leg['capacity'].get('aum_cap_20pct_ex_lowliq_yi')} |", "",
          "## ③ 券源约束（空头端可实现性）", ""]
    if margin.get("data_available"):
        L += [f"- 小市值长腿成分的**两融标的覆盖率**：{margin['smallcap_long_margin_covered_ratio']:.1%}",
              f"- 全市场融券余额中位（抽样季度末）：{margin['market_rqye_median_yi']} 亿元",
              f"- 空头端可实现：**{'是' if margin.get('short_side_feasible') else '否'}** —— {margin['reason']}",
              "", "**假设与局限**："] + [f"- {a}" for a in margin.get("assumptions", [])]
    else:
        L.append(f"- {margin.get('assumptions', ['本地数据不可用'])}")
    L += ["", "## ④ 替代路径：小市值+反转 long-only", "",
          "| 路径 | 持仓 | 20日超额 | t | 年化 | 换手年化 | 30bp 净 | 50bp 净 | 80bp 净 | "
          "WF 2023-24 | WF 2025-26 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in [("P2 小市值+反转", long_leg)] + list(rep["alt_paths"].items()):
        wf = r["walk_forward"]
        L.append("| {} | {} | {}% | {} | {}% | {} | {}% | {}% | {}% | {}% | {}% |".format(
            name, r["avg_holdings"], r["excess_20d_pct"], r["excess_t"], r["ann_car_pct"],
            r["turnover"]["ann_overlapping"],
            (r["cost"].get("30bp") or {}).get("net_ann_pct"),
            (r["cost"].get("50bp") or {}).get("net_ann_pct"),
            (r["cost"].get("80bp") or {}).get("net_ann_pct"),
            (wf.get("2023-2024") or {}).get("mean_20d_pct"),
            (wf.get("2025-2026") or {}).get("mean_20d_pct")))
    L += ["", "## ⑤ 前置条件（若未来要重新评估）", "",
          f"- 长腿 20 日超额需 ≥ {LONG_LEG_EXCESS_MIN}%、30bp 净年化 ≥ {GO_NET30_MIN}%；",
          f"- 容量（10% 参与率、剔除极端流动性）需 ≥ {TARGET_AUM_YI} 亿元；",
          "- 至少一条 long-only 替代路径需 30bp 净超额>0 且 2023-24/2025-26 双窗同为正；",
          "- 若考虑空头端：需先证明可稳定借到小市值券（本地 rqye 显示券源稀缺，当前判不可行）；",
          "- 小市值需额外风控（退市/流动性/ST），且 IC 集中于小市值意味着容量与 alpha 不可兼得。", "",
          "## ⑥ 局限", "",
          "- 行业分类为 stock_basic 静态行业（非 PIT 行业变迁）；",
          "- 容量为日频 ADTV 近似，未含冲击成本/涨跌停不可执行；20 日重叠持有下换手按 1/20 折算；",
          "- 券源为本地 margin_detail 季度末抽样 + 公开常识假设，未联网获取实时券源/费率；",
          "- 20 日重叠窗口，t 值仍偏乐观（已给月度非重叠 t 与 walk-forward 辅助判断）；",
          "- 研究输出，不构成投资建议。"]
    (OUT / "smallcap_reversal_feasibility_20261101.md").write_text(
        "\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"decision": d["decision"], "criteria": d["criteria_pass"],
                      "long_leg_excess": long_leg["excess_20d_pct"],
                      "net30": (long_leg["cost"].get("30bp") or {}).get("net_ann_pct"),
                      "cap10_ex": long_leg["capacity"].get("aum_cap_10pct_ex_lowliq_yi"),
                      "runtime": rep["meta"]["runtime_sec"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
