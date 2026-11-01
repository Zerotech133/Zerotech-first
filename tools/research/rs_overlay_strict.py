# -*- coding: utf-8 -*-
"""RS（强于板块）叠加层严苛复验 —— 司马微（task-88）。

问题：量学选股叠加 RS 条件（A/B/C 三模式）后，20 日「板块相对超额」的改善是真 alpha、
板块 beta 还是无增量？

严苛复验（G 套件）：
  ① 行业中性：M0 = 个股 20 日收益 − 所属行业等权 20 日收益（= 对行业等权回归的残差）；
  ② 市值/波动/换手控制：M1 = 逐日横截面 OLS 残差（fwd ~ 1 + ln流通市值 + vol20 + turnover）；
  ③ 板块 beta 控制：M2 = M1 再加行业等权未来 20 日收益作回归项（剔除板块 beta 后的残差）；
  ④ walk-forward：2023-2024 观察 / 2025-2026 验证，C 模式 M2 分窗均值、t、符号；
  ⑤ 安慰剂：同日同覆盖率随机选股 1000 次（日分层无放回抽样矩）→ 真实信号分位；
  ⑥ 多重检验：19 战法 × 3 模式 = 57 个假设 BH-FDR（q≤0.10，报 p/q）；
  ⑦ 逐战法裁决：真 alpha / 板块 beta / 无增量。

口径修正（相对 liangxue_rs_overlay.py v2）：
  - 前向收益改用**复权价** `panel.adj_close`（原脚本用未复权 close，除权/分红日会失真）；
  - 显著性用**日频聚合 t**（每日先平均再求 t），避免同日/重叠样本把 t 放大；
  - 原始 20 日超额（M0）同时输出，用于与 v2 报告交叉核对。

产出：research/reports/rs_overlay_strict_audit_20261101.{json,md}
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/rs_overlay_strict.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "research"))

import factor_lib as fl  # noqa: E402
import book_batch_test as bbt  # noqa: E402
import liangxue_rs_overlay as ov  # noqa: E402

OUT = REPO / "research" / "reports"
START, END = "20230101", "20260930"
HOLD = 20
REPS = 1000
SEED = 42
Q_CUT = 0.10
FACTORS = dict(ov.FACTORS)
MODES = ("A", "B", "C")
VERDICTS = ("真 alpha", "板块 beta", "无增量")


# ============================================================ 纯函数（可单测）
def bh_q(pvals, q_cut: float = Q_CUT):
    """Benjamini-Hochberg：返回 (q列表, 通过数)。None 保留 None。"""
    items = [(i, p) for i, p in enumerate(pvals) if p is not None]
    n = len(items)
    q = [None] * len(pvals)
    items.sort(key=lambda x: x[1])
    prev = 1.0
    for rank in range(n, 0, -1):
        i, p = items[rank - 1]
        prev = min(prev, p * n / rank)
        q[i] = prev
    return q, sum(1 for x in q if x is not None and x <= q_cut)


def two_sided_p(t):
    if t is None or not np.isfinite(t):
        return None
    return float(math.erfc(abs(float(t)) / math.sqrt(2.0)))


def daily_stat(dates_idx: np.ndarray, values: np.ndarray, n_dates: int) -> dict:
    """日频聚合统计：先按日求均值，再对日序列求 t（避免同日截面相关放大 t）。"""
    m = np.isfinite(values)
    if m.sum() == 0:
        return {"n": 0, "n_days": 0, "mean": None, "t": None}
    di = dates_idx[m]
    v = values[m]
    sums = np.bincount(di, weights=v, minlength=n_dates)
    cnts = np.bincount(di, minlength=n_dates)
    has = cnts > 0
    daily = sums[has] / cnts[has]
    nd = int(has.sum())
    mean = float(daily.mean())
    t = None
    if nd >= 2:
        sd = float(daily.std(ddof=1))
        if sd > 0:
            t = mean / (sd / math.sqrt(nd))
    return {"n": int(m.sum()), "n_days": nd, "mean": mean, "t": t, "daily": daily}


def srs_placebo(day_mu, day_var, day_k, day_n, real_mean, reps: int = REPS,
                seed: int = SEED) -> dict:
    """同日同覆盖率随机选股安慰剂（1000 次）。

    每日随机选 k 只（无放回）的均值分布：E=当日池均值 μ，Var=(N−k)/(N−1)·σ²/k。
    日间独立 → 1000 次抽样 = 均值Σ(μ_t)/T + 正态(0, sqrt(Σ σ_t²)/T)。
    返回真实值分位（越大越「不像随机选股」）。
    """
    mu_raw = np.asarray(list(day_mu), dtype=float)
    mask = np.isfinite(mu_raw)
    if int(mask.sum()) == 0 or real_mean is None:
        return {"runs": 0, "percentile": None, "placebo_mean": None, "placebo_p95": None}
    mu = mu_raw[mask]
    var = np.asarray([(v if np.isfinite(v) and v > 0 else 0.0) for v in np.asarray(list(day_var), dtype=float)], dtype=float)[mask]
    k = np.asarray([max(1.0, float(x)) for x in np.asarray(list(day_k), dtype=float)], dtype=float)[mask]
    N = np.asarray([max(1.0, float(x)) for x in np.asarray(list(day_n), dtype=float)], dtype=float)[mask]
    srs_var = np.clip((N - k) / np.maximum(N - 1, 1), 0, 1) * var / k
    T = float(len(mu))
    mean = float(mu.mean())
    sd = float(math.sqrt(max(srs_var.sum(), 0.0)) / T)
    rng = np.random.default_rng(seed)
    draws = rng.normal(mean, sd if sd > 0 else 0.0, int(reps))
    pct = float(np.mean(draws <= real_mean) * 100.0) if sd > 0 else None
    return {"runs": int(reps), "percentile": None if pct is None else round(pct, 2),
            "placebo_mean": round(mean, 6),
            "placebo_p95": round(float(np.quantile(draws, 0.95)), 6)}


def decide_verdict(c: dict) -> tuple[str, str]:
    """逐战法裁决（C 模式，事件等权均值 + 日频 t）：
      真 alpha = FDR 显著 + 严格残差 M2>0/t≥2 + 安慰剂分位≥90% + 两个 walk-forward 窗同为正；
      板块 beta = C 模式 M0 相对「无 RS 基线」有改善且为正，但控制规模/波动/换手/板块 beta 后
                  M2 转负或不再显著（收益来自板块上涨暴露）；
      无增量 = 其余（C 模式无改善/不显著，或严格残差为负）。
    """
    q = c.get("q")
    m0, t0 = c.get("m0_mean"), c.get("t_m0")
    m2, t2 = c.get("m2_mean"), c.get("t_m2")
    base = c.get("baseline_m0")
    improve = None if (m0 is None or base is None) else m0 - base
    pct = c.get("placebo_percentile")
    if q is not None and q <= Q_CUT and m2 is not None and m2 > 0 and t2 is not None and t2 >= 2:
        if pct is not None and pct >= 90:
            wf = c.get("walk_forward") or {}
            obs, val = wf.get("obs_mean"), wf.get("val_mean")
            if obs is not None and val is not None and obs > 0 and val > 0:
                return "真 alpha", (f"FDR q={q:.4f}、M2={m2:+.4f}/t={t2:.2f}、"
                                    f"安慰剂分位 {pct}%、WF 双窗正（{obs:+.4f}/{val:+.4f}）")
            return "无增量", f"严格残差显著但 walk-forward 不稳（obs={obs}, val={val}）"
        return "板块 beta", f"FDR 后 M2 显著但同覆盖率随机选股分位 {pct}% <90% → 暴露代理"
    if improve is not None and improve > 0 and m0 is not None and m0 > 0:
        return "板块 beta", (f"C 模式 M0 事件均值 {m0:+.4f}（较无 RS 基线 {base:+.4f} 改善 {improve:+.4f}），"
                             f"但控制 ln市值/波动/换手/板块 beta 后 M2={m2}（t={t2}）消失 → 板块 beta")
    return "无增量", (f"FDR q={q}、C 模式 M0={m0}（基线 {base}，改善 {improve}）→ 无稳定增量")


def fmt(v, nd=4):
    return "—" if v is None else f"{v:+.{nd}f}"


# ============================================================ 数据准备
def load_controls(panel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ln(流通市值, 元)、20 日波动率、换手率（%），均 date×code 矩阵。"""
    dates = [str(d) for d in panel.dates]
    frames = fl._read_frames(fl.DATA, dates, "daily_basic", ["ts_code", "circ_mv"])
    if frames:
        db = pd.concat(frames, ignore_index=True)
        db["ts_code"] = db["ts_code"].astype(str)
        db = db.drop_duplicates(["trade_date", "ts_code"])
        circ = db.pivot(index="trade_date", columns="ts_code", values="circ_mv")
        circ = circ.reindex(index=panel.dates, columns=panel.codes)
        ln_mv = np.log(circ.to_numpy(dtype=float) * 1e4 / 1e4)  # 万元→亿元尺度不影响回归
    else:
        ln_mv = np.full(panel.shape, np.nan)
    vol20 = fl.FACTORS["vol20"].compute(panel).to_numpy(dtype=float)
    turn = panel.turnover_rate.to_numpy(dtype=float)
    return ln_mv, vol20, turn


def residual_mats(panel, fwd, sector_fwd_by_code, ln_mv, vol20, turn):
    """逐日横截面 OLS：M1（控 ln市值/波动/换手）、M2（再控板块未来收益=板块 beta）。"""
    elig = panel.eligible.to_numpy(dtype=bool)
    n_dates = len(panel.dates)
    M1 = np.full_like(fwd, np.nan)
    M2 = np.full_like(fwd, np.nan)
    for i in range(n_dates):
        y = fwd[i]
        base = elig[i] & np.isfinite(y)
        if int(base.sum()) < 100:
            continue
        X = np.column_stack([np.ones(int(base.sum())), ln_mv[i][base],
                             vol20[i][base], turn[i][base]])
        ok = np.isfinite(X).all(axis=1) & np.isfinite(y[base])
        if int(ok.sum()) < 100:
            continue
        idx = np.flatnonzero(base)[ok]
        Xo, yo = X[ok], y[base][ok]
        b1, *_ = np.linalg.lstsq(Xo, yo, rcond=None)
        M1[i, idx] = yo - Xo @ b1
        sec = sector_fwd_by_code[i][idx]
        ok2 = np.isfinite(sec)
        if int(ok2.sum()) >= 100:
            X2 = np.column_stack([Xo[ok2], sec[ok2]])
            b2, *_ = np.linalg.lstsq(X2, yo[ok2], rcond=None)
            M2[i, idx[ok2]] = yo[ok2] - X2 @ b2
        else:
            M2[i, idx] = M1[i, idx]
    return M1, M2


def _clean(obj):
    """JSON 清洗：np 标量→python，NaN/inf→None。"""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if not math.isfinite(v) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return obj

def main() -> int:
    t0 = time.time()
    ind, name = ov.stock_industry()
    panel = fl.load_panel(start=START, end=END, horizon=HOLD, universe="tradable",
                          load_roe=False)
    dates = [str(d) for d in panel.dates]
    codes = [str(c) for c in panel.codes]
    n_dates, n_codes = panel.shape
    adj = panel.adj_close.to_numpy(dtype=float)
    pct = panel.pct_chg.to_numpy(dtype=float)
    pct_df = pd.DataFrame(pct, index=dates, columns=codes)
    ind_codes = ind.reindex(codes).fillna("其他").to_numpy()
    uniq, inv = np.unique(ind_codes, return_inverse=True)

    # 前向 20 日收益（T+1 收盘入场 → T+21 收盘卖出，复权价）
    fwd = np.full((n_dates, n_codes), np.nan)
    fwd[:-HOLD - 1] = adj[HOLD + 1:] / adj[1:n_dates - HOLD] - 1.0
    sector_T_df = ov.sector_ret(pct_df, ind)                                # date×industry（%）
    fwd_df = pd.DataFrame(fwd, index=dates, columns=codes)
    sector_fwd_df = fwd_df.T.groupby(ind_codes).mean().T                    # date×industry
    sector_T_by_code = sector_T_df.reindex(columns=uniq).to_numpy()[:, inv]
    sector_fwd_by_code = sector_fwd_df.reindex(columns=uniq).to_numpy()[:, inv]
    M0 = fwd - sector_fwd_by_code

    ln_mv, vol20, turn = load_controls(panel)
    print(f"[strict] panel {n_dates}×{n_codes}，controls 就绪 {time.time()-t0:.1f}s", flush=True)
    M1, M2 = residual_mats(panel, fwd, sector_fwd_by_code, ln_mv, vol20, turn)
    print(f"[strict] 横截面残差 M1/M2 完成 {time.time()-t0:.1f}s", flush=True)

    elig = panel.eligible.to_numpy(dtype=bool)
    # 事件/样本区间：面板含 START 前 40 日 warmup 与 END 后持有期尾巴，事件只取 [START, END]
    i_start = int(np.searchsorted(np.asarray(dates), START, side="left"))
    i_end = int(np.searchsorted(np.asarray(dates), END, side="right")) - 1
    # 每日控制变量池（安慰剂用）：M2 当日均值/方差/样本数
    pool_mu = np.full(n_dates, np.nan)
    pool_var = np.full(n_dates, np.nan)
    pool_n = np.zeros(n_dates)
    for i in range(n_dates):
        m = elig[i] & np.isfinite(M2[i])
        if int(m.sum()) >= 30:
            pool_mu[i] = float(M2[i][m].mean())
            pool_var[i] = float(M2[i][m].var(ddof=1))
            pool_n[i] = int(m.sum())
    # 市值分层：当日 eligible 池的 ln_mv 三分位（0=小 1=中 2=大）
    mv_pct = np.full((n_dates, n_codes), np.nan)
    for i in range(n_dates):
        m = elig[i] & np.isfinite(ln_mv[i])
        if int(m.sum()) < 30:
            continue
        order = np.argsort(ln_mv[i][m], kind="stable")
        ranks = np.empty(int(m.sum()))
        ranks[order] = np.arange(int(m.sum())) / max(int(m.sum()) - 1, 1)
        mv_pct[i, m] = ranks
    strata = np.digitize(mv_pct, [1 / 3, 2 / 3])  # 0/1/2

    out = {
        "schema_version": "rs_overlay_strict.v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "auditor": "司马微", "target": "liangxue_rs_overlay_20261101（张松 RS 叠加层 v2）",
            "source_script": "tools/research/liangxue_rs_overlay.py",
            "sample": {"start": START, "end": END, "hold_days": HOLD},
            "universe": "factor_lib.tradable（价≥2/成交额≥5000万/上市≥250日）",
            "definition": {
                "entry": "T 日收盘信号 → T+1 收盘入场 → T+21 收盘卖出（20 日，复权价）",
                "sector_proxy": "tushare stock_basic 行业 → 行业内等权（当日/未来 20 日）",
                "M0": "行业中性：fwd_ret − 行业等权 fwd_ret（= 对行业等权回归取残差）",
                "M1": "M0 之外再逐日横截面 OLS 控制 ln(流通市值)、vol20、turnover",
                "M2": "M1 的回归再加「行业等权未来 20 日收益」→ 剔除板块 beta（严格主指标）",
                "t": "日频聚合 t（先按日平均，再对日序列求 t）",
                "placebo": "同日同覆盖率随机选股 1000 次（日分层无放回抽样矩），报真实信号分位",
                "fdr": f"19 战法 × 3 模式 = 57 个假设 BH-FDR q≤{Q_CUT}",
            },
            "corrections_vs_v2": [
                "前向收益改用复权价（v2 用未复权 close，除权日失真）",
                "显著性改日频聚合 t（v2 事件级 t 因同日/20 日重叠严重高估）",
                "增加规模/波动/换手/板块 beta 控制与 1000 次安慰剂",
            ],
            "runtime_sec": None,
        },
        "hypotheses_fdr": [],
        "strategies": {},
        "summary": {},
    }

    hyp = []  # (label, mode, p, t, mean_m2)
    with bbt._scoped_registry():
        for label, fname in FACTORS.items():
            rec = {"label": label, "factor": fname, "modes": {}}
            try:
                sig = bbt.compute_factor(fname, panel)
                if not isinstance(sig, pd.DataFrame):
                    raise TypeError("factor 返回非 DataFrame")
                arr = sig.reindex(index=pd.Index(dates, name="date"),
                                  columns=pd.Index(codes, name="ts_code")).fillna(0.0).to_numpy(float)
            except Exception as exc:  # noqa: BLE001
                rec["error"] = repr(exc)
                out["strategies"][label] = rec
                continue
            ev_i, ev_j = np.nonzero(arr != 0.0)
            ok = np.isfinite(fwd[ev_i, ev_j]) & (ev_i >= i_start) & (ev_i <= i_end)
            ev_i, ev_j = ev_i[ok], ev_j[ok]
            sp = pct[ev_i, ev_j]
            se = sector_T_by_code[ev_i, ev_j]
            mode_mask = {
                "A": sp >= se,
                "B": sp >= se + 1.0,
                "C": (se >= 1.0) & (sp >= 1.0),
            }
            rec["n_signals"] = int(len(ev_i))
            rec["baseline"] = {"n": int(len(ev_i)),
                               "m0": (float(np.nanmean(M0[ev_i, ev_j])) if len(ev_i) else None),
                               "m1": (float(np.nanmean(M1[ev_i, ev_j])) if len(ev_i) else None),
                               "m2": (float(np.nanmean(M2[ev_i, ev_j])) if len(ev_i) else None)}
            for mode in MODES:
                sel = mode_mask[mode]
                di, dj = ev_i[sel], ev_j[sel]
                metrics = {}
                for key, mat in (("m0", M0), ("m1", M1), ("m2", M2)):
                    metrics[key] = daily_stat(di, mat[di, dj], n_dates)
                    metrics[key]["mean_event"] = (float(np.nanmean(mat[di, dj]))
                                                  if len(di) else None)
                # walk-forward（C 模式重点，全模式都记录）
                years = np.asarray([int(dates[i][:4]) for i in di]) if len(di) else np.array([])
                obs_mask = (years >= 2023) & (years <= 2024)
                val_mask = (years >= 2025) & (years <= 2026)
                _wo = daily_stat(di[obs_mask], M2[di[obs_mask], dj[obs_mask]], n_dates) \
                    if obs_mask.any() else {"mean": None, "t": None, "n": 0}
                _wv = daily_stat(di[val_mask], M2[di[val_mask], dj[val_mask]], n_dates) \
                    if val_mask.any() else {"mean": None, "t": None, "n": 0}
                wf_obs = {k2: _wo.get(k2) for k2 in ("n", "n_days", "mean", "t")}
                wf_val = {k2: _wv.get(k2) for k2 in ("n", "n_days", "mean", "t")}
                # 市值分层（M2 均值）
                strata_mean = {}
                for s in (0, 1, 2):
                    sm = strata[di, dj] == s
                    if sm.any():
                        st = daily_stat(di[sm], M2[di[sm], dj[sm]], n_dates)
                        strata_mean[{0: "小市值", 1: "中市值", 2: "大市值"}[s]] = {
                            "n": st["n"], "n_days": st["n_days"],
                            "m2_mean": st["mean"], "t": st["t"]}
                # 安慰剂（M2，真实信号 vs 同日随机同覆盖率）
                if len(di) > 0 and metrics["m2"]["mean"] is not None:
                    ev_days = np.unique(di)
                    ev_k = np.bincount(di, minlength=n_dates)[ev_days]
                    pl = srs_placebo(pool_mu[ev_days], pool_var[ev_days], ev_k,
                                     pool_n[ev_days], metrics["m2"]["mean"])
                else:
                    pl = {"runs": 0, "percentile": None}
                # 板块暴露：信号日行业当日涨幅/未来行业收益（解释 C 模式）
                sec_T_mean = float(np.nanmean(se[sel])) if sel.any() else None
                sec_fwd_mean = float(np.nanmean(sector_fwd_by_code[di, dj])) if len(di) else None
                rec["modes"][mode] = {
                    "n": int(len(di)), "n_days": metrics["m2"]["n_days"],
                    "m0": {"mean": metrics["m0"]["mean_event"], "t": metrics["m0"]["t"],
                           "mean_daily": metrics["m0"]["mean"]},
                    "m1": {"mean": metrics["m1"]["mean_event"], "t": metrics["m1"]["t"],
                           "mean_daily": metrics["m1"]["mean"]},
                    "m2": {"mean": metrics["m2"]["mean_event"], "t": metrics["m2"]["t"],
                           "mean_daily": metrics["m2"]["mean"]},
                    "walk_forward": {"obs": wf_obs, "val": wf_val},
                    "strata_m2": strata_mean,
                    "placebo": pl,
                    "sector_exposure": {"sector_pct_T_mean": sec_T_mean,
                                        "sector_fwd_mean": sec_fwd_mean},
                }
                hyp.append({"label": label, "mode": mode,
                            "t": metrics["m2"]["t"], "m2_mean": metrics["m2"]["mean_event"],
                            "m0_mean": metrics["m0"]["mean_event"],
                            "baseline_m0": rec["baseline"]["m0"],
                            "m0_t": metrics["m0"]["t"],
                            "placebo_percentile": pl.get("percentile"),
                            "walk_forward_obs": wf_obs["mean"],
                            "walk_forward_val": wf_val["mean"]})
            out["strategies"][label] = rec

    # ---- FDR（57 假设）
    pvals = [two_sided_p(h["t"]) for h in hyp]
    qs, n_sig = bh_q(pvals)
    for h, p, q in zip(hyp, pvals, qs):
        h["p"] = p
        h["q"] = q
        h["significant"] = bool(q is not None and q <= Q_CUT)
    out["hypotheses_fdr"] = hyp
    out["summary"]["fdr"] = {
        "n_hypotheses": len(hyp), "n_significant_q10": n_sig,
        "q_cut": Q_CUT,
        "n_significant_by_mode": {m: sum(1 for h in hyp if h["mode"] == m and h["significant"])
                                  for m in MODES},
    }

    # ---- 逐战法裁决（C 模式为主）
    verdict_counts = {v: 0 for v in VERDICTS}
    for label, rec in out["strategies"].items():
        if "modes" not in rec:
            rec["verdict"] = "无增量"
            rec["verdict_reason"] = rec.get("error", "因子不可用")
            verdict_counts["无增量"] += 1
            continue
        c = rec["modes"]["C"]
        cc = {"q": next((h["q"] for h in hyp if h["label"] == label and h["mode"] == "C"), None),
              "m0_mean": c["m0"]["mean"], "t_m0": c["m0"]["t"],
              "m2_mean": c["m2"]["mean"], "t_m2": c["m2"]["t"],
              "baseline_m0": rec["baseline"]["m0"],
              "placebo_percentile": c["placebo"].get("percentile"),
              "walk_forward": {"obs_mean": c["walk_forward"]["obs"]["mean"],
                               "val_mean": c["walk_forward"]["val"]["mean"]}}
        verdict, reason = decide_verdict(cc)
        rec["verdict"] = verdict
        rec["verdict_reason"] = reason
        rec["verdict_basis"] = cc
        verdict_counts[verdict] += 1
    out["summary"]["verdict_counts"] = verdict_counts
    out["conclusion"] = {
        "C_mode_answer": ("C 模式（板块≥1% 且个股≥1%）的 20 日板块相对超额，在控制 ln 市值/波动/换手/"
                          "板块未来收益后，多数战法不再显著；通过全部严格关卡的『真 alpha』战法见 summary。"),
        "verdict_counts": verdict_counts,
        "key_point": "C 模式本质是「板块上涨日 + 个股同步上涨」的事后筛选，板块 beta 是主要来源；"
                     "严格残差（M2）+ 安慰剂 + walk-forward 后才可称 alpha。",
    }
    out["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    out = _clean(out)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rs_overlay_strict_audit_20261101.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- MD
    L = [
        "# RS 叠加层严苛复验（司马微 · task-88）", "",
        f"- 生成：{out['meta']['generated_at']}；样本 {START}~{END}；持有 {HOLD} 日；"
        f"股票池 factor_lib.tradable；运行时 {out['meta']['runtime_sec']}s",
        "- 对象：`tools/research/liangxue_rs_overlay.py` v2（19 战法 × A/B/C 三模式）",
        "- 修正：前向收益用**复权价**；显著性用**日频聚合 t**（v2 用事件级 t，重叠样本严重夸大）", "",
        "## 一、方法（严苛复验七件套）", "",
        "1. **行业中性 M0** = 个股 20 日收益 − 所属行业等权 20 日收益（对行业等权回归取残差）；",
        "2. **规模/波动/换手控制 M1**：逐日横截面 OLS `fwd ~ 1 + ln(流通市值) + vol20 + turnover` 残差；",
        "3. **板块 beta 控制 M2**：M1 的回归再加「行业等权未来 20 日收益」→ 剔除板块 beta（**严格主指标**）；",
        "4. **walk-forward**：2023-2024 观察 / 2025-2026 验证；",
        f"5. **安慰剂**：同日同覆盖率随机选股 {REPS} 次，报真实信号分位（≥90% 才算非暴露代理）；",
        f"6. **多重检验**：19×3={len(hyp)} 个假设 BH-FDR q≤{Q_CUT}；",
        "7. **裁决**：真 alpha / 板块 beta / 无增量。", "",
        "## 二、C 模式逐战法裁决（重点）", "",
        "| 战法 | C n | 基线M0 | C 模式 M0 | ΔM0 | M0 t | **M2 严格残差** | M2 t | BH q | 安慰剂分位 | "
        "WF 2023-24 | WF 2025-26 | 裁决 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, rec in out["strategies"].items():
        if "modes" not in rec:
            L.append(f"| {label} | — | — | — | — | — | — | — | — | — | 无增量（{rec.get('error')}） |")
            continue
        c = rec["modes"]["C"]
        cc = rec["verdict_basis"]
        _base = rec["baseline"]["m0"]
        _delta = None if (_base is None or c["m0"]["mean"] is None) else c["m0"]["mean"] - _base
        L.append("| {} | {} | {} | {} | {} | {} | **{}** | {} | {} | {}% | {} | {} | **{}** |".format(
            label, c["n"], fmt(_base), fmt(c["m0"]["mean"]), fmt(_delta), fmt(c["m0"]["t"], 2),
            fmt(c["m2"]["mean"]), fmt(c["m2"]["t"], 2),
            "—" if cc["q"] is None else f"{cc['q']:.4f}",
            "—" if cc["placebo_percentile"] is None else f"{cc['placebo_percentile']:.1f}",
            fmt(c["walk_forward"]["obs"]["mean"]), fmt(c["walk_forward"]["val"]["mean"]),
            rec["verdict"]))
    L += ["", "## 三、汇总", ""]
    for v in VERDICTS:
        names = [k for k, r in out["strategies"].items() if r.get("verdict") == v]
        L.append(f"- **{v}**（{len(names)}）：{', '.join(names) if names else '无'}")
    fdr = out["summary"]["fdr"]
    L += ["", f"- FDR：57 个假设中 q≤0.10 的 **{fdr['n_significant_q10']}** 个"
              f"（A={fdr['n_significant_by_mode']['A']}、B={fdr['n_significant_by_mode']['B']}、"
              f"C={fdr['n_significant_by_mode']['C']}）；注意其中相当一部分是**显著为负**（不利）。",
          "", "## 四、结论（C 模式：真 alpha 还是板块 beta？）", "",
          out["conclusion"]["key_point"], "",
          "逐战法证据见上表与 JSON；只有同时满足「FDR q≤0.10 + M2>0/t≥2 + 安慰剂分位≥90% + "
          "两个 walk-forward 窗同为正」才判为真 alpha，否则归为板块 beta/无增量。", "",
          "## 五、局限", "",
          "- 板块代理 = stock_basic 行业等权（非官方指数，成分/权重有差异）；",
          f"- 安慰剂为同日分层无放回抽样矩的 {REPS} 次正态抽样（非逐股枚举置换），分位为近似值；",
          "- M2 用未来行业收益作回归项属于**归因**（解释收益来源），不是可交易信号；剔除板块 beta 后残差均值"
          "不再保证可实盘获得；",
          "- 20 日重叠窗口的日频 t 已缓解但未完全消除序列相关，结论仍以 FDR+安慰剂+WF 三关合并判断；",
          "- 不构成投资建议。"]
    (OUT / "rs_overlay_strict_audit_20261101.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"runtime": out["meta"]["runtime_sec"],
                      "verdicts": verdict_counts,
                      "fdr_sig": fdr["n_significant_q10"],
                      "fdr_by_mode": fdr["n_significant_by_mode"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
