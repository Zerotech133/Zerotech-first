"""B1 先验版本复合因子 + 策略回测（P0 基线 / P1 A 股反转先验）。

用法：
  python tools/research/run_b1_prior_strategy.py --prior-version p0 --suite oos \
      --out research/reports/b1_prior_p0_oos_20261105.json
  python tools/research/run_b1_prior_strategy.py --prior-version p1 --suite oos \
      --out research/reports/b1_prior_p1_oos_20261105.json

原则：
- 方向由 factor_prior_variants 提供；P0 与 P1 互不覆盖。
- 权重不做 IC 优化：先验签名 → 逐日截面 rank → 族内等权 → 族间等权。
- 回测窗口固定；P1 的 holdout 是时间外推，不是严格预注册 OOS（见报告）。
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib as f  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402
import factor_prior_variants as pv  # noqa: E402
from factor_portfolio import PortfolioConfig, simulate  # noqa: E402
from factor_neutralize import neutralize_matrix  # noqa: E402
from pit_industry import load_sw_l2_industry_with_snapshot_fallback  # noqa: E402

DEFAULT_OUT = ROOT / "research" / "reports" / "b1_prior_strategy_20261105.json"
FAMILIES = (
    "Tushare-动量",
    "Tushare-流动性",
    "Tushare-风险",
    "Tushare-反转",
    "Tushare-规模",
)
MIN_FAMILY_FACTORS = 2
MIN_FAMILIES = 3
YEAR_LIST = (2023, 2024, 2025, 2026)


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


def build_composite(panel: f.Panel, directions: dict[str, int]) -> tuple[pd.DataFrame, list[dict]]:
    """按指定先验方向构建族平衡复合因子；返回 (composite, diagnostics)。"""
    family_mats: dict[str, list[pd.DataFrame]] = {fam: [] for fam in FAMILIES}
    diag: list[dict] = []
    t0 = time.time()
    factor_names = list(pv.B1_P1_STRATEGY_FACTORS)
    for i, name in enumerate(factor_names, 1):
        spec = f.FACTORS[name]
        direction = int(directions[name])
        values = spec.compute(panel)
        if not isinstance(values, pd.DataFrame):
            raise TypeError(f"{name} 返回 {type(values)}")
        values = values.replace([np.inf, -np.inf], np.nan)
        signed = values * float(direction)
        ranked = signed.rank(axis=1, pct=True) - 0.5
        ranked = ranked.reindex(index=panel.dates, columns=panel.codes)
        family_mats[spec.family].append(ranked)
        valid_n = int(ranked.notna().sum().sum())
        diag.append({
            "name": name,
            "family": spec.family,
            "direction": direction,
            "p0_direction": int(ft.B1_PRIOR_DIRECTIONS[name]),
            "rationale": (
                "P1: A 股反转先验，动量族整体反向"
                if direction != int(ft.B1_PRIOR_DIRECTIONS[name])
                else ft.B1_PRIOR_RATIONALE.get(name, "")
            ),
            "valid_cells": valid_n,
        })
        if i % 10 == 0:
            print(f"[composite] {i}/{len(factor_names)} {time.time()-t0:.1f}s", flush=True)

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


def spec_for(matrix: pd.DataFrame, name: str, version: str) -> f.FactorSpec:
    def compute(p):
        return matrix.reindex(index=p.dates, columns=p.codes)
    return f.FactorSpec(
        name=name,
        family=f"B1-先验复合-{version.upper()}",
        kind="continuous",
        direction=1,
        description=f"B1 {version.upper()} 先验方向族平衡复合（未知方向因子排除）",
        source=f"b1_prior_{version}_composite",
        requires=(),
        compute=compute,
    )


def compact_eval(report: dict, factor_name: str) -> dict:
    meta = report.get("meta", {})
    effective = meta.get("effective") if isinstance(meta, dict) else None
    return {
        "meta": {
            "effective": effective,
            "is_end": meta.get("is_end") if isinstance(meta, dict) else None,
            "runtime_sec": meta.get("runtime_sec") if isinstance(meta, dict) else None,
        },
        "aggregate": report.get("aggregate", {}),
        "baseline": report.get("baseline"),
        "factor": report.get("factors", {}).get(factor_name, {}),
    }


def compact_agg(report: dict, factor_name: str) -> dict:
    meta = report.get("meta", {})
    return {
        "meta": {"effective": meta.get("effective"), "is_end": meta.get("is_end")},
        "factor": report.get("factors", {}).get(factor_name, {}),
        "baseline": report.get("baseline"),
    }


def _series_payload(series: pd.Series) -> dict:
    return {
        "dates": [str(x) for x in series.index],
        "values": [None if pd.isna(x) else float(x) for x in series.to_numpy()],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-version", choices=pv.PRIOR_VERSIONS, default="p0")
    parser.add_argument("--suite", choices=("full", "oos"), default="full")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start", default="20230101")
    parser.add_argument("--end", default="20261104")
    args = parser.parse_args(argv)

    prior = pv.get_prior(args.prior_version)
    t0 = time.time()
    print(f"[start] prior={prior.version} suite={args.suite} out={args.out}", flush=True)
    print("[start] load_panel", flush=True)
    panel = f.load_panel(start=args.start, end=args.end)
    print(f"[start] panel={panel.shape} dates={panel.dates[0]}..{panel.dates[-1]} load={time.time()-t0:.1f}s", flush=True)

    print("[start] load PIT L2 fallback + names", flush=True)
    pit = load_sw_l2_industry_with_snapshot_fallback()
    names = load_names()
    print(f"[start] pit={pit.source}", flush=True)

    composite, diag = build_composite(panel, prior.directions)
    print(f"[composite] built {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    neut, neut_info = neutralize_matrix(panel, composite, industry=True, size=True, industry_map=pit)
    print(f"[neutralize] {time.time()-t1:.1f}s info={neut_info}", flush=True)

    def eval_matrix(matrix: pd.DataFrame, name: str, start: str, end: str, is_end: str) -> dict:
        spec = spec_for(matrix, name, prior.version)
        return f.evaluate_panel(
            panel,
            factor_names=[name],
            factor_specs={name: spec},
            start=start, end=end, is_end=is_end,
            horizon=20, cost=0.003, universe="tradable", n_boot=50,
        )

    print("[eval] full IC", flush=True)
    ic_raw = eval_matrix(composite, "b1_composite_raw", "20230101", "20261104", "20250101")
    ic_neut = eval_matrix(neut, "b1_composite_neut", "20230101", "20261104", "20250101")

    print("[eval] dev/holdout IC", flush=True)
    ic_windows: dict[str, dict] = {}
    for tag, start, end, split in (
        ("dev", "20230103", "20241231", "20240101"),
        ("holdout", "20250102", "20260929", "20250102"),
    ):
        ic_windows[tag] = {
            "raw": compact_agg(
                eval_matrix(composite, f"b1_composite_raw_{tag}", start, end, split),
                f"b1_composite_raw_{tag}",
            ),
            "neutral": compact_agg(
                eval_matrix(neut, f"b1_composite_neut_{tag}", start, end, split),
                f"b1_composite_neut_{tag}",
            ),
        }

    print("[eval] yearly IC", flush=True)
    yearly: dict[str, dict] = {}
    for year in YEAR_LIST:
        start = f"{year}0101"
        end = "20261104" if year == 2026 else f"{year}1231"
        split = f"{year}0701"
        yearly[str(year)] = {
            "raw": compact_agg(
                eval_matrix(composite, f"b1_composite_raw_{year}", start, end, split),
                f"b1_composite_raw_{year}",
            ),
            "neutral": compact_agg(
                eval_matrix(neut, f"b1_composite_neut_{year}", start, end, split),
                f"b1_composite_neut_{year}",
            ),
        }

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
            "daily_returns": _series_payload(r["daily_returns"]),
        }

    if args.suite == "full":
        windows = (
            ("long", "20230403", "20261031"),
            ("fresh", "20250102", "20260929"),
        )
    else:
        windows = (
            ("dev", "20230403", "20241231"),
            ("holdout", "20250102", "20260929"),
        )

    portfolio: list[dict] = []
    for tag, start, end in windows:
        for days in (40, 60):
            portfolio.append(run_portfolio(neut, f"neut_{tag}", days, 30, start, end, True))
        if tag in ("holdout", "fresh"):
            portfolio.append(run_portfolio(neut, f"neut_{tag}", 20, 30, start, end, True))
            portfolio.append(run_portfolio(composite, f"raw_{tag}", 60, 30, start, end, False))
            portfolio.append(run_portfolio(neut, f"neut_{tag}", 60, 0, start, end, True))
            portfolio.append(run_portfolio(neut, f"neut_{tag}", 60, 50, start, end, True))

    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "prior_version": prior.version,
        "prior": {
            "version": prior.version,
            "label": prior.label,
            "meta": pv.prior_meta(prior.version),
            "directions": prior.directions,
            "rationale": ft.B1_PRIOR_RATIONALE,
            "unknown": sorted(ft.B1_PRIOR_UNKNOWN),
            "strategy_factors": list(pv.B1_P1_STRATEGY_FACTORS),
            "family_counts": {fam: sum(1 for n in pv.B1_P1_STRATEGY_FACTORS if f.FACTORS[n].family == fam) for fam in FAMILIES},
        },
        "panel": {
            "shape": list(panel.shape),
            "first": panel.dates[0],
            "last": panel.dates[-1],
            "runtime_sec": round(time.time() - t0, 1),
        },
        "composite": {
            "diagnostics": diag,
            "ic_full_raw": compact_eval(ic_raw, "b1_composite_raw"),
            "ic_full_neutral": compact_eval(ic_neut, "b1_composite_neut"),
            "ic_windows": ic_windows,
            "ic_yearly": yearly,
            "neutralization": neut_info,
        },
        "portfolio": portfolio,
        "meta": {
            "suite": args.suite,
            "pit_source": pit.source,
            "note": "先验方向硬编码；权重不基于 IC；P0/P1 独立落盘，P1 不覆盖 P0",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] out={out} runtime={time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
