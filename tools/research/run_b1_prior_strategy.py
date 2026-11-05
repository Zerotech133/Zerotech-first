"""B1 先验方向复合因子 + 策略回测（60d，PIT L2 行业/市值约束）。

先验方向在 factor_lib_tushare.B1_PRIOR_DIRECTIONS 中硬编码；本脚本只做
「按先验方向签名 → 截面分位 → 族内等权 → 族间等权」的复合，不再用样本内
IC 调整方向/权重。随后做 20/40/60 日组合回测与成本敏感性。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib as f  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402
from factor_portfolio import PortfolioConfig, simulate  # noqa: E402
from factor_neutralize import neutralize_matrix  # noqa: E402
from pit_industry import load_sw_l2_industry_with_snapshot_fallback  # noqa: E402

OUT = ROOT / "research" / "reports" / "b1_prior_strategy_20261105.json"
FAMILIES = (
    "Tushare-动量",
    "Tushare-流动性",
    "Tushare-风险",
    "Tushare-反转",
    "Tushare-规模",
)
MIN_FAMILY_FACTORS = 2
MIN_FAMILIES = 3


def _mean_frames(frames: list[pd.DataFrame], index, columns) -> pd.DataFrame:
    """按元素对多个 date×code DataFrame 求均值（忽略 NaN）。"""
    arr = np.stack([df.reindex(index=index, columns=columns).to_numpy(dtype=float) for df in frames], axis=0)
    with np.errstate(invalid="ignore"):
        out = np.nanmean(arr, axis=0)
    return pd.DataFrame(out, index=index, columns=columns)


def load_names() -> dict[str, str]:
    import glob
    names: dict[str, str] = {}
    for path in glob.glob(str(ROOT / "quant_backtest" / "data" / "tushare" / "stock_basic" / "part_*.parquet")):
        df = pd.read_parquet(path, columns=["ts_code", "name"])
        names.update(dict(zip(df["ts_code"].astype(str), df["name"].astype(str))))
    return names


def build_composite(panel: f.Panel) -> tuple[pd.DataFrame, list[dict]]:
    """按先验方向构建族平衡复合因子；返回 (composite, diagnostics)。"""
    family_mats: dict[str, list[pd.DataFrame]] = {fam: [] for fam in FAMILIES}
    diag: list[dict] = []
    t0 = time.time()
    for i, name in enumerate(ft.B1_STRATEGY_FACTORS, 1):
        spec = f.FACTORS[name]
        values = spec.compute(panel)
        if not isinstance(values, pd.DataFrame):
            raise TypeError(f"{name} 返回 {type(values)}")
        values = values.replace([np.inf, -np.inf], np.nan)
        signed = values * float(spec.direction)
        ranked = signed.rank(axis=1, pct=True) - 0.5
        ranked = ranked.reindex(index=panel.dates, columns=panel.codes)
        family_mats[spec.family].append(ranked)
        valid_n = int(ranked.notna().sum().sum())
        diag.append({
            "name": name,
            "family": spec.family,
            "direction": int(spec.direction),
            "rationale": ft.B1_PRIOR_RATIONALE.get(name, ""),
            "valid_cells": valid_n,
        })
        if i % 10 == 0:
            print(f"[composite] {i}/{len(ft.B1_STRATEGY_FACTORS)} {time.time()-t0:.1f}s", flush=True)

    fam_composites: dict[str, pd.DataFrame] = {}
    for fam, mats in family_mats.items():
        if len(mats) < MIN_FAMILY_FACTORS:
            continue
        fam_composites[fam] = _mean_frames(mats, panel.dates, panel.codes)
        print(f"[composite] family={fam} factors={len(mats)}", flush=True)

    if len(fam_composites) < MIN_FAMILIES:
        raise RuntimeError(f"可用因子族不足: {list(fam_composites)}")
    fam_names = list(fam_composites)
    stacked = np.stack([fam_composites[f].to_numpy(dtype=float) for f in fam_names], axis=0)
    with np.errstate(invalid="ignore"):
        composite_arr = np.nanmean(stacked, axis=0)
    coverage = np.sum(np.isfinite(stacked), axis=0)
    composite_arr[coverage < MIN_FAMILIES] = np.nan
    composite = pd.DataFrame(composite_arr, index=panel.dates, columns=panel.codes)
    print(f"[composite] families={len(fam_composites)} coverage_median={float(np.median(coverage)):.1f}", flush=True)
    return composite, diag


def compact_eval(report: dict, factor_name: str) -> dict:
    meta = report.get("meta", {})
    agg = report.get("aggregate", {})
    keep = {
        "meta": {k: meta.get(k) for k in ("effective", "is_end", "split_source", "factors_evaluated", "runtime_sec")},
        "aggregate": agg,
        "baseline": report.get("baseline"),
        "factor": report.get("factors", {}).get(factor_name, {}),
    }
    return keep


def main() -> int:
    t0 = time.time()
    print("[start] load_panel", flush=True)
    panel = f.load_panel(start="20230101", end="20261104")
    print(f"[start] panel={panel.shape} dates={panel.dates[0]}..{panel.dates[-1]} load={time.time()-t0:.1f}s", flush=True)

    print("[start] load PIT L2 fallback + names", flush=True)
    pit = load_sw_l2_industry_with_snapshot_fallback()
    names = load_names()
    print(f"[start] pit={pit.source}", flush=True)

    composite, diag = build_composite(panel)
    print(f"[composite] built {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    neut, neut_info = neutralize_matrix(panel, composite, industry=True, size=True, industry_map=pit)
    print(f"[neutralize] {time.time()-t1:.1f}s info={neut_info}", flush=True)

    def spec_for(matrix: pd.DataFrame, name: str) -> f.FactorSpec:
        def compute(p):
            return matrix.reindex(index=p.dates, columns=p.codes)
        return f.FactorSpec(
            name=name,
            family="B1-先验复合",
            kind="continuous",
            direction=1,
            description="B1 先验方向族平衡复合（未知方向因子排除）",
            source="b1_prior_composite",
            requires=(),
            compute=compute,
        )

    print("[eval] composite IC", flush=True)
    ic_raw = f.evaluate_panel(
        panel,
        factor_names=["b1_composite_prior"],
        factor_specs={"b1_composite_prior": spec_for(composite, "b1_composite_prior")},
        start="20230101", end="20261104", is_end="20250101",
        horizon=20, cost=0.003, universe="tradable", n_boot=50,
    )
    ic_neut = f.evaluate_panel(
        panel,
        factor_names=["b1_composite_prior_neutral"],
        factor_specs={"b1_composite_prior_neutral": spec_for(neut, "b1_composite_prior_neutral")},
        start="20230101", end="20261104", is_end="20250101",
        horizon=20, cost=0.003, universe="tradable", n_boot=50,
    )
    print(f"[eval] IC raw={ic_raw.get('aggregate', {})}", flush=True)
    print(f"[eval] IC neut={ic_neut.get('aggregate', {})}", flush=True)

    def run_portfolio(matrix: pd.DataFrame, label: str, days: int, cost_bp: float,
                      start: str, end: str, constrained: bool) -> dict:
        cfg = PortfolioConfig(
            rebalance_days=days,
            quantile=0.2,
            buffer_quantile=0.3,
            turnover_penalty=0.5,
            industry_neutral=constrained,
            size_neutral=constrained,
            max_one_way_turnover=0.3,
            cost_bp=cost_bp,
        )
        r = simulate(panel, matrix, cfg, names=names, start=start, end=end, industry_map=pit)
        return {
            "label": label,
            "days": days,
            "cost_bp": cost_bp,
            "start": start,
            "end": end,
            "constrained": constrained,
            "metrics": r["metrics"],
            "exposures": r.get("exposures", {}),
        }

    portfolio: list[dict] = []
    for start, end, tag in (
        ("20230403", "20261031", "long"),
        ("20250102", "20260929", "fresh"),
    ):
        for days in (20, 40, 60):
            portfolio.append(run_portfolio(neut, f"neut_{tag}", days, 30, start, end, True))
        portfolio.append(run_portfolio(composite, f"raw_{tag}", 60, 30, start, end, False))
        portfolio.append(run_portfolio(neut, f"neut_{tag}", 60, 0, start, end, True))
        portfolio.append(run_portfolio(neut, f"neut_{tag}", 60, 50, start, end, True))

    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "panel": {
            "shape": list(panel.shape),
            "first": panel.dates[0],
            "last": panel.dates[-1],
            "runtime_sec": round(time.time() - t0, 1),
        },
        "prior": {
            "directions": ft.B1_PRIOR_DIRECTIONS,
            "rationale": ft.B1_PRIOR_RATIONALE,
            "unknown": sorted(ft.B1_PRIOR_UNKNOWN),
            "strategy_factors": list(ft.B1_STRATEGY_FACTORS),
            "family_counts": {fam: sum(1 for n in ft.B1_STRATEGY_FACTORS if f.FACTORS[n].family == fam) for fam in FAMILIES},
        },
        "composite": {
            "diagnostics": diag,
            "ic_raw": compact_eval(ic_raw, "b1_composite_prior"),
            "ic_neutral": compact_eval(ic_neut, "b1_composite_prior_neutral"),
            "neutralization": neut_info,
        },
        "portfolio": portfolio,
        "meta": {
            "pit_source": pit.source,
            "note": "先验方向硬编码；复合权重不基于 IC；20日CAR IC 与组合回测口径见报告",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] out={OUT} runtime={time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
