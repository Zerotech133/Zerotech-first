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


def _weighted_mean_frames(frames: list[pd.DataFrame], weights: list[float], index, columns) -> pd.DataFrame:
    """按元素加权平均；每个格子只对非 NaN 的因子重新归一化权重。"""
    arr = np.stack([df.reindex(index=index, columns=columns).to_numpy(dtype=float) for df in frames], axis=0)
    w = np.asarray(weights, dtype=float).reshape((-1, 1, 1))
    mask = np.isfinite(arr)
    with np.errstate(invalid="ignore", divide="ignore"):
        wsum = np.sum(np.where(mask, w, 0.0), axis=0)
        out = np.sum(np.where(mask, arr * w, 0.0), axis=0) / wsum
    return pd.DataFrame(out, index=index, columns=columns)


def load_names() -> dict[str, str]:
    import glob
    names: dict[str, str] = {}
    for path in glob.glob(str(ROOT / "quant_backtest" / "data" / "tushare" / "stock_basic" / "part_*.parquet")):
        df = pd.read_parquet(path, columns=["ts_code", "name"])
        names.update(dict(zip(df["ts_code"].astype(str), df["name"].astype(str))))
    return names


def build_composite(
    panel: f.Panel,
    directions: dict[str, int],
    factor_names: list[str] | None = None,
    momentum_weights: dict[str, float] | None = None,
    extra_specs: dict | None = None,
    tilt_weight: float | None = None,
    tilt_factor: str | None = None,
    return_families: bool = False,
) -> tuple[pd.DataFrame, list[dict]] | tuple[pd.DataFrame, list[dict], dict[str, pd.DataFrame]]:
    """按指定先验方向构建族平衡复合因子；返回 (composite, diagnostics)。

    P0/P1 使用每族等权；P2 传入 `momentum_weights` 时，动量族内部按预注册
    周期权重合成 sleeve，其余族仍等权。P3 传入 `tilt_weight`/`tilt_factor`
    时，只在动量 sleeve 内部叠加 21 日 tilt，族间权重保持不变。
    `return_families=True` 时额外返回 `fam_composites`，供归因/敏感性研究复用。
    """
    family_mats: dict[str, list[pd.DataFrame]] = {fam: [] for fam in FAMILIES}
    momentum_mats: list[pd.DataFrame] = []
    momentum_used: list[str] = []
    diag: list[dict] = []
    t0 = time.time()
    specs = dict(f.FACTORS)
    if extra_specs:
        specs.update(extra_specs)
    if factor_names is None:
        factor_names = list(pv.B1_P1_STRATEGY_FACTORS)
    for i, name in enumerate(factor_names, 1):
        if name not in specs:
            raise KeyError(f"未注册因子: {name}")
        spec = specs[name]
        direction = int(directions[name])
        values = spec.compute(panel)
        if not isinstance(values, pd.DataFrame):
            raise TypeError(f"{name} 返回 {type(values)}")
        values = values.replace([np.inf, -np.inf], np.nan)
        signed = values * float(direction)
        ranked = signed.rank(axis=1, pct=True) - 0.5
        ranked = ranked.reindex(index=panel.dates, columns=panel.codes)
        is_weighted_momentum = spec.family == "Tushare-动量" and momentum_weights is not None
        if is_weighted_momentum:
            momentum_mats.append(ranked)
            momentum_used.append(name)
        else:
            family_mats[spec.family].append(ranked)
        valid_n = int(ranked.notna().sum().sum())
        p0_direction = ft.B1_PRIOR_DIRECTIONS.get(name)
        diag.append({
            "name": name,
            "family": spec.family,
            "direction": direction,
            "p0_direction": int(p0_direction) if p0_direction is not None else None,
            "weight": float(momentum_weights[name]) if is_weighted_momentum else None,
            "rationale": (
                "P1: A 股反转先验，动量族整体反向"
                if p0_direction is not None and direction != int(p0_direction)
                else (
                    "P2: 21 日核心动量 sleeve 的预注册周期权重"
                    if is_weighted_momentum
                    else ft.B1_PRIOR_RATIONALE.get(name, "")
                )
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
    if momentum_weights is not None:
        if len(momentum_mats) < MIN_FAMILY_FACTORS:
            raise RuntimeError("P2 动量 sleeve 可用因子不足")
        weights = [float(momentum_weights.get(name, 0.0)) for name in momentum_used]
        total = float(sum(weights))
        if total <= 0:
            raise ValueError("P2 动量 sleeve 权重和必须为正")
        weights = [w / total for w in weights]
        fam_composites["Tushare-动量"] = _weighted_mean_frames(
            momentum_mats, weights, panel.dates, panel.codes
        )
        print(
            f"[composite] family=Tushare-动量 weighted factors={len(momentum_mats)} "
            f"weights={dict(zip(momentum_used, weights))}",
            flush=True,
        )
    if tilt_weight is not None:
        if tilt_factor is None:
            raise ValueError("P3 tilt 需要同时提供 tilt_factor")
        if abs(float(tilt_weight)) > 1e-12:
            if "Tushare-动量" not in fam_composites:
                raise RuntimeError("P3 tilt 需要可用的动量 sleeve")
            tilt_spec = pv.p2_momentum_specs()[tilt_factor]
            tilt_values = tilt_spec.compute(panel)
            if not isinstance(tilt_values, pd.DataFrame):
                raise TypeError(f"tilt 因子 {tilt_factor} 返回 {type(tilt_values)}")
            tilt_values = tilt_values.replace([np.inf, -np.inf], np.nan)
            tilt_rank = (tilt_values * float(tilt_spec.direction)).rank(axis=1, pct=True) - 0.5
            tilt_rank = tilt_rank.reindex(index=panel.dates, columns=panel.codes)
            fam_composites["Tushare-动量"] = _weighted_mean_frames(
                [fam_composites["Tushare-动量"], tilt_rank],
                [1.0 - float(tilt_weight), float(tilt_weight)],
                panel.dates,
                panel.codes,
            )
            diag.append({
                "name": tilt_factor,
                "family": "Tushare-动量",
                "direction": int(tilt_spec.direction),
                "p0_direction": None,
                "weight": float(tilt_weight),
                "rationale": "P3: 仅动量 sleeve 内部 21 日 tilt，族间权重不变",
                "valid_cells": int(tilt_rank.notna().sum().sum()),
            })
            print(
                f"[composite] momentum_tilt factor={tilt_factor} weight={float(tilt_weight):.3f}",
                flush=True,
            )

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
    if return_families:
        return composite, diag, fam_composites
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
    parser.add_argument(
        "--tilt-weight",
        type=float,
        default=None,
        help="P3 动量 sleeve 内的 21 日 tilt 权重；仅 p3 可用，必须在预注册候选内",
    )
    args = parser.parse_args(argv)
    if args.tilt_weight is not None and args.prior_version != "p3":
        parser.error("--tilt-weight 只适用于 --prior-version p3")

    prior = pv.get_prior(args.prior_version, tilt_weight=args.tilt_weight)
    t0 = time.time()
    print(f"[start] prior={prior.version} suite={args.suite} out={args.out}", flush=True)
    print("[start] load_panel", flush=True)
    panel = f.load_panel(start=args.start, end=args.end)
    print(f"[start] panel={panel.shape} dates={panel.dates[0]}..{panel.dates[-1]} load={time.time()-t0:.1f}s", flush=True)

    print("[start] load PIT L2 fallback + names", flush=True)
    pit = load_sw_l2_industry_with_snapshot_fallback()
    names = load_names()
    print(f"[start] pit={pit.source}", flush=True)

    extra_specs = pv.p2_momentum_specs() if prior.version == "p2" else None
    all_specs = dict(f.FACTORS)
    if extra_specs:
        all_specs.update(extra_specs)
    composite, diag = build_composite(
        panel,
        prior.directions,
        factor_names=list(prior.strategy_factors),
        momentum_weights=prior.momentum_weights,
        extra_specs=extra_specs,
        tilt_weight=prior.tilt_weight,
        tilt_factor=prior.tilt_factor,
    )
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
            "meta": pv.prior_meta(prior.version, tilt_weight=prior.tilt_weight),
            "directions": prior.directions,
            "rationale": ft.B1_PRIOR_RATIONALE,
            "unknown": sorted(ft.B1_PRIOR_UNKNOWN),
            "strategy_factors": list(prior.strategy_factors),
            "momentum_weights": prior.momentum_weights,
            "tilt_weight": prior.tilt_weight,
            "tilt_factor": prior.tilt_factor,
            "family_counts": {fam: sum(1 for n in prior.strategy_factors if all_specs[n].family == fam) for fam in FAMILIES},
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
