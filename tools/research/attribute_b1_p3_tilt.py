"""B1 P3 21 日 tilt 归因：拆解 P3 相对 P1 的增量收益来源。

研究用途，不改 P0/P1/P2/P3 预注册。核心问题：
1. P3-P1 的增量收益集中在哪些年份/月份、行业、市值分组、个股？
2. 增量收益能被哪些因子暴露解释（21d exact、P1 动量 sleeve、五族）？
3. 21d tilt 是否特异：与 5/60/120d tilt、return_21d tilt、随机 placebo 对照。

方法：
- 重建 P1 族矩阵，按 P3 公式在动量 sleeve 内叠加 tilt，保证与 P3 回测口径一致。
- 同一配置跑 60d 中性组合（2025-01-02..2026-09-29，30bp），记录每日权重。
- 用 P3/P1 每日权重差 × 个股日收益做精确持仓归因（行业/市值/个股/时间）。
- 用因子多空组合日收益对 P3-P1 日增量收益做回归，给出因子暴露解释。
"""
from __future__ import annotations

import hashlib
import json
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib as f  # noqa: E402
import factor_prior_variants as pv  # noqa: E402
from factor_neutralize import neutralize_matrix  # noqa: E402
from factor_portfolio import PortfolioConfig, investable_masks, simulate  # noqa: E402
from pit_industry import load_sw_l2_industry_with_snapshot_fallback  # noqa: E402
from run_b1_prior_strategy import (  # noqa: E402
    _weighted_mean_frames,
    build_composite,
    load_names,
)

PANEL_START = "20230101"
PANEL_END = "20261104"
HOLDOUT_START = "20250102"
HOLDOUT_END = "20260929"
TILT_WEIGHT = 0.075
FAMILIES = (
    "Tushare-动量",
    "Tushare-流动性",
    "Tushare-风险",
    "Tushare-反转",
    "Tushare-规模",
)


def _rank(panel: f.Panel, name: str, direction: int | None = None,
          extra_specs: dict | None = None) -> pd.DataFrame:
    specs = dict(f.FACTORS)
    if extra_specs:
        specs.update(extra_specs)
    spec = specs[name]
    if direction is None:
        direction = int(spec.direction)
    values = spec.compute(panel).replace([np.inf, -np.inf], np.nan)
    signed = values * float(direction)
    ranked = signed.rank(axis=1, pct=True) - 0.5
    return ranked.reindex(index=panel.dates, columns=panel.codes)


def _combine_families(families: dict[str, pd.DataFrame], tilt_rank: pd.DataFrame,
                      tilt_weight: float) -> pd.DataFrame:
    """P1 族矩阵 + 动量 sleeve 内 tilt -> P3 口径复合因子。"""
    fam = dict(families)
    fam["Tushare-动量"] = _weighted_mean_frames(
        [families["Tushare-动量"], tilt_rank],
        [1.0 - float(tilt_weight), float(tilt_weight)],
        tilt_rank.index,
        tilt_rank.columns,
    )
    names = list(fam)
    stacked = np.stack([fam[name].to_numpy(dtype=float) for name in names], axis=0)
    with np.errstate(invalid="ignore"):
        arr = np.nanmean(stacked, axis=0)
    coverage = np.sum(np.isfinite(stacked), axis=0)
    arr[coverage < 3] = np.nan
    return pd.DataFrame(arr, index=tilt_rank.index, columns=tilt_rank.columns)


def _simulate(panel, matrix, cfg, names, pit, capture: bool = False) -> dict:
    neut, info = neutralize_matrix(panel, matrix, industry=True, size=True, industry_map=pit)
    r = simulate(
        panel, neut, cfg, names=names, start=HOLDOUT_START, end=HOLDOUT_END,
        industry_map=pit, capture_weights=capture,
    )
    return {"neutral": neut, "info": info, "result": r}


def _active_daily(p1: dict, p3: dict, panel: f.Panel) -> tuple[pd.Series, list[dict]]:
    rets = panel.fields["adj_close"].pct_change()
    m1 = {x["date"]: x["weights"] for x in p1.get("daily_weights", [])}
    m3 = {x["date"]: x["weights"] for x in p3.get("daily_weights", [])}
    rows: list[dict] = []
    for d in sorted(set(m1) & set(m3)):
        if d not in rets.index:
            continue
        w1 = pd.Series(m1[d], dtype=float)
        w3 = pd.Series(m3[d], dtype=float)
        active = w3.sub(w1, fill_value=0.0)
        r = rets.loc[d]
        contrib = active * r.reindex(active.index).fillna(0.0)
        rows.append({
            "date": d,
            "active_return": float(contrib.sum()),
            "gross_p1": float((w1 * r.reindex(w1.index).fillna(0.0)).sum()),
            "gross_p3": float((w3 * r.reindex(w3.index).fillna(0.0)).sum()),
            "contrib": contrib,
            "active_weights": active,
        })
    ser = pd.Series([x["active_return"] for x in rows], index=[x["date"] for x in rows], dtype=float)
    return ser, rows


def _factor_ls_returns(rank: pd.DataFrame, panel: f.Panel, mask: pd.DataFrame,
                       quantile: float = 0.2) -> pd.Series:
    """因子多空组合日收益：t-1 打分，t 日收益；多头/空头各 0.5。"""
    rets = panel.fields["adj_close"].pct_change()
    dates = panel.dates
    out = pd.Series(np.nan, index=dates, dtype=float)
    for i in range(1, len(dates)):
        score = rank.iloc[i - 1]
        m = mask.iloc[i - 1].fillna(False).to_numpy(dtype=bool)
        valid = score[score.notna() & m]
        if len(valid) < 20:
            continue
        k = max(1, int(np.floor(len(valid) * quantile)))
        vals = valid.sort_values(ascending=False)
        longs = vals.index[:k]
        shorts = vals.index[-k:]
        r = rets.iloc[i].reindex(valid.index).fillna(0.0)
        out.iloc[i] = float(0.5 * r.loc[longs].mean() - 0.5 * r.loc[shorts].mean())
    return out


def _regression(y: pd.Series, factors: pd.DataFrame) -> dict:
    df = pd.concat([y.rename("y"), factors], axis=1).dropna()
    if len(df) < 40:
        return {"error": "insufficient observations", "n": int(len(df))}
    yv = df["y"].to_numpy(dtype=float)
    xv = df.drop(columns=["y"]).to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(xv)), xv])
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    dof = max(1, len(yv) - X.shape[1])
    sigma2 = float((resid @ resid) / dof)
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = np.full(len(beta), np.nan)
    t = beta / np.where(se > 0, se, np.nan)
    mean_f = df.drop(columns=["y"]).mean()
    contrib = {name: float(beta[j + 1] * mean_f[name] * 252.0) for j, name in enumerate(mean_f.index)}
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = float(1.0 - (resid @ resid) / ss_tot) if ss_tot > 0 else None
    return {
        "n": int(len(df)),
        "alpha_ann": float(beta[0] * 252.0),
        "r2": r2,
        "coefficients": {
            name: {
                "beta": float(beta[j + 1]),
                "t": None if not np.isfinite(t[j + 1]) else float(t[j + 1]),
                "contrib_ann": contrib[name],
                "mean_factor_daily": float(mean_f[name]),
            }
            for j, name in enumerate(mean_f.index)
        },
    }


def _top(series: pd.Series, n: int = 10) -> list[dict]:
    out = []
    for k, v in series.sort_values().items():
        out.append({"name": str(k), "value": float(v)})
    return out[-n:][::-1] + out[:n]


def main() -> int:
    t0 = time.time()
    print("[attr] load panel/pit/names", flush=True)
    panel = f.load_panel(start=PANEL_START, end=PANEL_END)
    pit = load_sw_l2_industry_with_snapshot_fallback()
    names = load_names()
    print(f"[attr] panel={panel.shape} pit={pit.source.get('level')} {time.time()-t0:.1f}s", flush=True)

    cfg = PortfolioConfig(
        rebalance_days=60, quantile=0.2, buffer_quantile=0.3,
        turnover_penalty=0.5, industry_neutral=True, size_neutral=True,
        max_one_way_turnover=0.3, cost_bp=30,
    )
    p1 = pv.get_prior("p1")
    pv.get_prior("p3", tilt_weight=TILT_WEIGHT)

    print("[attr] build P1 families", flush=True)
    comp1, diag1, fam1 = build_composite(
        panel, p1.directions, factor_names=list(p1.strategy_factors),
        return_families=True,
    )
    print(f"[attr] P1 families={list(fam1)} {time.time()-t0:.1f}s", flush=True)

    p2_specs = pv.p2_momentum_specs()
    ranks: dict[str, pd.DataFrame] = {}
    for name in p2_specs:
        ranks[name] = _rank(panel, name, direction=-1, extra_specs=p2_specs)
    ranks["return_21d"] = _rank(panel, "return_21d", direction=int(p1.directions["return_21d"]))
    rng = np.random.default_rng(20261106)
    random_scores = pd.DataFrame(
        rng.standard_normal(panel.shape), index=panel.dates, columns=panel.codes
    )
    ranks["random_placebo"] = random_scores.rank(axis=1, pct=True) - 0.5

    comp3 = _combine_families(fam1, ranks[pv.P3_TILT_FACTOR], TILT_WEIGHT)

    variants: dict[str, tuple[pd.DataFrame, str]] = {
        "p1": (comp1, "P1 baseline"),
        "p3_21d": (comp3, "P3 main tilt 7.5% exact 21d"),
        "tilt_5d": (_combine_families(fam1, ranks["b1_mom_5d"], TILT_WEIGHT), "tilt 5d"),
        "tilt_60d": (_combine_families(fam1, ranks["b1_mom_60d"], TILT_WEIGHT), "tilt 60d"),
        "tilt_120d": (_combine_families(fam1, ranks["b1_mom_120d"], TILT_WEIGHT), "tilt 120d"),
        "tilt_return_21d": (_combine_families(fam1, ranks["return_21d"], TILT_WEIGHT), "tilt P1 return_21d"),
        "tilt_random": (_combine_families(fam1, ranks["random_placebo"], TILT_WEIGHT), "random placebo tilt"),
    }

    results: dict[str, dict] = {}
    checkpoint = Path(r"C:\Temp\p3_attribution_checkpoint.pkl")
    meta_fp = hashlib.sha256(json.dumps({
        "shape": list(panel.shape), "first": str(panel.dates[0]), "last": str(panel.dates[-1]),
        "codes": len(panel.codes), "tilt": TILT_WEIGHT, "cfg": cfg.to_dict(),
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    reuse = "--reuse" in sys.argv and checkpoint.exists()
    if reuse:
        with checkpoint.open("rb") as fh:
            payload = pickle.load(fh)
        if isinstance(payload, dict) and payload.get("__meta__") == meta_fp:
            results = payload.get("results", {})
            print(f"[attr] reuse checkpoint={checkpoint} variants={list(results)}", flush=True)
        else:
            reuse = False
            print("[attr] checkpoint fingerprint mismatch; recompute", flush=True)
    for name, (matrix, desc) in ([] if reuse else variants.items()):
        print(f"[attr] simulate {name}", flush=True)
        out = _simulate(panel, matrix, cfg, names, pit, capture=name in ("p1", "p3_21d"))
        results[name] = {
            "description": desc,
            "metrics": out["result"]["metrics"],
            "neutralization": out["info"],
            "daily_returns": out["result"]["daily_returns"],
            "trades": out["result"]["trades"],
            "daily_weights": out["result"].get("daily_weights", []),
        }
        print(f"[attr] {name} ann={out['result']['metrics']['ann_return']} "
              f"sharpe={out['result']['metrics']['sharpe']} {time.time()-t0:.1f}s", flush=True)

    with checkpoint.open("wb") as fh:
        pickle.dump({"__meta__": meta_fp, "results": results}, fh,
                    protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[attr] checkpoint={checkpoint}", flush=True)

    print("[attr] reconcile P3/P1", flush=True)
    active, rows = _active_daily(results["p1"], results["p3_21d"], panel)
    p3_metrics = results["p3_21d"]["metrics"]
    p1_metrics = results["p1"]["metrics"]
    gross_p3 = float((1.0 + results["p3_21d"]["daily_returns"]).prod() - 1.0)
    gross_p1 = float((1.0 + results["p1"]["daily_returns"]).prod() - 1.0)
    cost3 = sum(float(t["cost"]) for t in results["p3_21d"]["trades"])
    cost1 = sum(float(t["cost"]) for t in results["p1"]["trades"])
    recon = {
        "p3_daily_sum": float(results["p3_21d"]["daily_returns"].sum()),
        "p1_daily_sum": float(results["p1"]["daily_returns"].sum()),
        "p3_compounded": gross_p3,
        "p1_compounded": gross_p1,
        "active_sum": float(active.sum()),
        "active_compounded_diff": gross_p3 - gross_p1,
        "p3_cost": cost3, "p1_cost": cost1, "cost_diff": cost3 - cost1,
        "p3_ann": p3_metrics["ann_return"], "p1_ann": p1_metrics["ann_return"],
        "p3_sharpe": p3_metrics["sharpe"], "p1_sharpe": p1_metrics["sharpe"],
        "p3_mdd": p3_metrics["max_drawdown"], "p1_mdd": p1_metrics["max_drawdown"],
    }
    print("[attr] recon", recon, flush=True)

    # 时间归因
    by_year: dict[str, dict] = {}
    for year, g in active.groupby(pd.Index(active.index).str[:4]):
        by_year[str(year)] = {
            "sum": float(g.sum()), "ann": float((1 + g).prod() ** (252 / len(g)) - 1),
            "days": int(len(g)), "positive_days": int((g > 0).sum()),
        }
    by_month = {
        f"{str(k)[:4]}-{str(k)[4:6]}": float(v)
        for k, v in active.groupby(pd.Index(active.index).str[:6]).sum().items()
    }

    # 行业/市值/个股归因
    stock_contrib = pd.Series(dtype=float)
    stock_active = pd.Series(dtype=float)
    industry_contrib: dict[str, float] = {}
    size_contrib: dict[str, float] = {"small": 0.0, "mid": 0.0, "large": 0.0}
    for rec in rows:
        d = rec["date"]
        contrib = rec["contrib"]
        stock_contrib = stock_contrib.add(contrib, fill_value=0.0)
        stock_active = stock_active.add(rec["active_weights"], fill_value=0.0)
        ind = pit.at(d).reindex(contrib.index).fillna("UNKNOWN").astype(str)
        for iname, val in contrib.groupby(ind).sum().items():
            industry_contrib[iname] = industry_contrib.get(iname, 0.0) + float(val)
        mv = panel.fields["total_mv"].loc[d].reindex(contrib.index)
        lmv = np.log(mv.where(mv > 0))
        try:
            buckets = pd.qcut(lmv.rank(method="first"), 3, labels=["small", "mid", "large"], duplicates="drop")
            for bname, val in contrib.groupby(buckets).sum().items():
                size_contrib[str(bname)] += float(val)
        except ValueError:
            pass

    top_stocks = []
    for code in stock_contrib.abs().sort_values(ascending=False).head(20).index:
        top_stocks.append({
            "code": str(code), "name": names.get(str(code), str(code)),
            "contrib": float(stock_contrib.get(code, 0.0)),
            "avg_active_weight": float(stock_active.get(code, 0.0) / max(1, len(rows))),
        })

    # 因子暴露：主动权重对因子的平均暴露
    factor_exposure: dict[str, float] = {}
    for fname, rank in {**{f"mom_{k}": v for k, v in ranks.items()},
                        **{f"fam_{k}": v for k, v in fam1.items()}}.items():
        vals = []
        for rec in rows:
            d = rec["date"]
            score = rank.loc[d].reindex(rec["active_weights"].index)
            vals.append(float((rec["active_weights"] * score.fillna(0.0)).sum()))
        factor_exposure[fname] = float(np.nanmean(vals)) if vals else 0.0

    # 因子多空收益回归
    print("[attr] factor long-short returns", flush=True)
    sel_mask, _ = investable_masks(panel, names, cfg)
    factor_rets = {}
    for fname, rank in {**{f"fam_{k}": v for k, v in fam1.items()},
                        "mom_21d": ranks["b1_mom_21d"],
                        "mom_5d": ranks["b1_mom_5d"],
                        "mom_60d": ranks["b1_mom_60d"],
                        "mom_120d": ranks["b1_mom_120d"],
                        "return_21d": ranks["return_21d"]}.items():
        factor_rets[fname] = _factor_ls_returns(rank, panel, sel_mask)
    fr = pd.DataFrame(factor_rets)
    reg_families = _regression(active, fr[[f"fam_{x}" for x in FAMILIES] + ["mom_21d"]])
    # return_21d 与 mom_21d 截面秩完全相同（P3 归因已证 1.0），同时回归会奇异。
    reg_all = _regression(active, fr.drop(columns=["return_21d"]))

    out = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": {
            "panel": {"shape": list(panel.shape), "first": str(panel.dates[0]),
                      "last": str(panel.dates[-1])},
            "holdout": [HOLDOUT_START, HOLDOUT_END],
            "tilt_weight": TILT_WEIGHT,
            "pit_source": pit.source,
            "config": cfg.to_dict(),
            "note": "研究归因；P3/P1 预注册不变，不接生产。",
        },
        "variants": {
            k: {
                "description": v["description"],
                "metrics": v["metrics"],
                "n_trades": len(v["trades"]),
            }
            for k, v in results.items()
        },
        "reconciliation": recon,
        "active": {
            "sum": float(active.sum()),
            "ann": float((1 + active).prod() ** (252 / len(active)) - 1),
            "daily_std": float(active.std(ddof=0)),
            "sharpe_ann": float(active.mean() / active.std(ddof=0) * np.sqrt(252)) if active.std(ddof=0) > 0 else None,
            "by_year": by_year,
            "by_month": by_month,
        },
        "attribution": {
            "by_industry": dict(sorted(industry_contrib.items(), key=lambda kv: abs(kv[1]), reverse=True)),
            "by_size": size_contrib,
            "top_stocks": top_stocks,
            "factor_exposure": factor_exposure,
        },
        "factor_regression": {
            "families_plus_21d": reg_families,
            "all_factors": reg_all,
        },
        "factor_stats": {
            "b1_mom_21d_vs_return_21d_corr": None,
        },
    }

    # 21d 与 return_21d 的截面相关性（抽样，避免日频全量过慢）
    sample_dates = list(panel.dates[::20])
    corrs = []
    for d in sample_dates:
        a = ranks["b1_mom_21d"].loc[d]
        b = ranks["return_21d"].loc[d]
        both = pd.concat([a, b], axis=1).dropna()
        if len(both) > 30:
            corrs.append(float(both.iloc[:, 0].corr(both.iloc[:, 1])))
    out["factor_stats"]["b1_mom_21d_vs_return_21d_corr"] = {
        "mean": float(np.nanmean(corrs)) if corrs else None,
        "median": float(np.nanmedian(corrs)) if corrs else None,
        "n_days": len(corrs),
    }

    out_path = ROOT / "research" / "reports" / "b1_p3_tilt_attribution_20261106.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[attr] done out={out_path} runtime={time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
