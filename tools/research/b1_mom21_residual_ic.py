"""b1_mom_21d 截面正交化后的独立 IC 检验。

研究用途，不改 P0/P1/P2/P3 预注册。核心问题：
- b1_mom_21d 与 P1 已有的 return_21d 是否只是同一暴露？
- 对 return_21d、P1 19 因子动量 sleeve、两者联合做逐日截面正交化后，
  残差是否还有独立 IC？

口径：
- 因子方向统一转成“正值 = 预期未来 20/60 日 CAR 更高”的 directed rank
  （rank(pct)-0.5 后乘先验方向；动量族方向 -1）。
- 每个交易日做横截面 OLS：y = α + β1*x1 + ... + ε，残差 ε 作为新因子。
- 用 factor_lib.evaluate_panel 在 dev / holdout / full 三个窗口评测，
  前向 CAR 为 T+1 收盘入场、T+1+h 收盘卖出、减等权市场收益与 30bp。
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

import factor_lib as fl  # noqa: E402
import factor_prior_variants as pv  # noqa: E402
from factor_lib_tushare import TUSHARE_FACTOR_MAP  # noqa: E402

PANEL_START = "20230101"
PANEL_END = "20261104"
HOLDOUT_START = "20250102"
HOLDOUT_END = "20260929"
DEV_START = "20230403"
DEV_END = "20241231"
MIN_N = 50
HORIZONS = (20, 60)
WINDOWS = {
    "dev": (DEV_START, DEV_END),
    "holdout": (HOLDOUT_START, HOLDOUT_END),
    "full": (DEV_START, HOLDOUT_END),
}
FACTOR_NAMES = (
    "b1_mom_21d",
    "return_21d",
    "p1_mom_sleeve",
    "b1_mom21_resid_vs_return21",
    "b1_mom21_resid_vs_p1_mom_sleeve",
)


def _directed_rank(values: pd.DataFrame, direction: int = -1) -> pd.DataFrame:
    """rank(pct)-0.5，再乘方向：正值 = 预期未来收益更高。"""
    v = values.replace([np.inf, -np.inf], np.nan)
    return (v * float(direction)).rank(axis=1, pct=True) - 0.5


def _momentum_names() -> list[str]:
    names = [
        name for name in pv.B1_P1_STRATEGY_FACTORS
        if (TUSHARE_FACTOR_MAP.get(name) or {}).get("factor_type") == pv.MOMENTUM_FACTOR_TYPE
    ]
    # 去重并保持 B1_P1_STRATEGY_FACTORS 顺序
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _mean_ranks(frames: list[pd.DataFrame], index: pd.Index,
                columns: pd.Index) -> pd.DataFrame:
    if not frames:
        raise ValueError("没有可合成的动量因子")
    arr = np.stack([f.to_numpy(dtype=float) for f in frames], axis=0)
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(arr, axis=0)
    return pd.DataFrame(mean, index=index, columns=columns)


def _orthogonalize(y: pd.DataFrame, xs: list[pd.DataFrame],
                   eligible: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """逐日截面对 y 回归 xs，返回残差与诊断。"""
    Y = y.to_numpy(dtype=float)
    Xs = [x.to_numpy(dtype=float) for x in xs]
    E = eligible.to_numpy(dtype=bool)
    out = np.full_like(Y, np.nan)
    coefs: list[list[float]] = []
    n_used = 0
    for i in range(Y.shape[0]):
        mask = E[i] & np.isfinite(Y[i])
        for X in Xs:
            mask &= np.isfinite(X[i])
        n = int(mask.sum())
        if n < MIN_N:
            continue
        design = np.column_stack([np.ones(n), *[X[i, mask] for X in Xs]])
        beta, *_ = np.linalg.lstsq(design, Y[i, mask], rcond=None)
        out[i, mask] = Y[i, mask] - design @ beta
        coefs.append([float(v) for v in beta])
        n_used += 1
    resid = pd.DataFrame(out, index=y.index, columns=y.columns)
    coef_arr = np.asarray(coefs, dtype=float) if coefs else np.empty((0, len(xs) + 1))
    diag = {
        "n_days": int(n_used),
        "n_obs": int(np.isfinite(out).sum()),
        "max_abs_residual": float(np.nanmax(np.abs(out))) if np.isfinite(out).any() else None,
        "mean_abs_residual": float(np.nanmean(np.abs(out))) if np.isfinite(out).any() else None,
        "mean_coef": [float(v) for v in coef_arr.mean(axis=0)] if len(coef_arr) else None,
    }
    return resid, diag


def _daily_rank_corr(a: pd.DataFrame, b: pd.DataFrame,
                     eligible: pd.DataFrame) -> dict:
    A = a.to_numpy(dtype=float)
    B = b.to_numpy(dtype=float)
    E = eligible.to_numpy(dtype=bool)
    vals: list[float] = []
    for i in range(A.shape[0]):
        m = E[i] & np.isfinite(A[i]) & np.isfinite(B[i])
        if int(m.sum()) < MIN_N:
            continue
        av = A[i, m] - A[i, m].mean()
        bv = B[i, m] - B[i, m].mean()
        den = float(np.sqrt((av * av).sum() * (bv * bv).sum()))
        if den <= 0:
            continue
        vals.append(float((av * bv).sum() / den))
    s = pd.Series(vals, dtype=float)
    return {
        "n_days": int(len(s)),
        "mean": float(s.mean()) if len(s) else None,
        "median": float(s.median()) if len(s) else None,
        "min": float(s.min()) if len(s) else None,
        "max": float(s.max()) if len(s) else None,
        "positive_rate": float((s > 0).mean()) if len(s) else None,
    }


def _factor_spec(name: str, matrix: pd.DataFrame, direction: int):
    def compute(panel):
        return matrix.reindex(index=panel.dates, columns=panel.codes)

    return fl.FactorSpec(
        name=name,
        family="research-b1-mom21-residual",
        kind="continuous",
        direction=int(direction),
        description="b1_mom_21d 截面正交化残差（研究用途）",
        source="tools/research/b1_mom21_residual_ic.py",
        requires=("adj_close",),
        compute=compute,
    )


def _extract(report: dict, name: str) -> dict:
    entry = report["factors"][name]
    out = {
        "n_obs": entry.get("n_obs"),
        "n_days": entry.get("n_days"),
        "ic": entry.get("ic"),
        "ic_directed": entry.get("ic_directed"),
        "by_year": entry.get("by_year"),
    }
    return out


def build(panel: fl.Panel, horizons=HORIZONS, n_boot: int = 100) -> dict:
    dates = panel.dates
    codes = panel.codes
    eligible = panel.eligible_mask()

    b1_raw = panel.fields["adj_close"] / panel.fields["adj_close"].shift(21) - 1.0
    r21_raw = fl.FACTORS["return_21d"].compute(panel)
    b1 = _directed_rank(b1_raw, -1)
    r21 = _directed_rank(r21_raw, -1)

    mom_names = _momentum_names()
    mom_ranks = [
        _directed_rank(fl.FACTORS[name].compute(panel),
                       pv.B1_P1_PRIOR_DIRECTIONS[name])
        for name in mom_names
    ]
    sleeve = _mean_ranks(mom_ranks, dates, codes)

    # 诊断：原始值是否完全一致 / 截面秩相关
    raw_diff = (b1_raw - r21_raw).abs()
    raw_exact = bool(raw_diff.where(eligible).max().max() < 1e-12)
    rank_corr = _daily_rank_corr(b1, r21, eligible)

    resid_r21, diag_r21 = _orthogonalize(b1, [r21], eligible)
    resid_sleeve, diag_sleeve = _orthogonalize(b1, [sleeve], eligible)

    # 与 return_21d 完全同源时，残差数值上恒为 0；此时 IC 无定义，
    # 不能把浮点噪声的排序当成“独立 IC”。
    degenerate_r21 = bool(
        diag_r21["max_abs_residual"] is not None
        and diag_r21["max_abs_residual"] < 1e-12
    )
    diag_r21["degenerate"] = degenerate_r21
    if degenerate_r21:
        resid_r21 = pd.DataFrame(np.nan, index=dates, columns=codes)

    # 残差方差占 b1 方向化 rank 方差的比例，用于判断“是否几乎被解释掉”
    b1_var = np.nanvar(b1.to_numpy(dtype=float), axis=1)
    resid_var = np.nanvar(resid_sleeve.to_numpy(dtype=float), axis=1)
    ratio = np.divide(resid_var, b1_var, out=np.full_like(resid_var, np.nan),
                      where=np.isfinite(b1_var) & (b1_var > 0))
    var_ratio = {
        "mean": float(np.nanmean(ratio)) if np.isfinite(ratio).any() else None,
        "median": float(np.nanmedian(ratio)) if np.isfinite(ratio).any() else None,
        "min": float(np.nanmin(ratio)) if np.isfinite(ratio).any() else None,
        "max": float(np.nanmax(ratio)) if np.isfinite(ratio).any() else None,
    }

    matrices = {
        "b1_mom_21d": b1,
        "return_21d": r21,
        "p1_mom_sleeve": sleeve,
        "b1_mom21_resid_vs_return21": resid_r21,
        "b1_mom21_resid_vs_p1_mom_sleeve": resid_sleeve,
    }
    # 所有矩阵都已经是 directed rank（正值 = 预期未来收益更高），
    # 因此 evaluate_panel 的方向统一为 +1，避免二次翻转。
    directions = {name: 1 for name in FACTOR_NAMES}
    specs = {name: _factor_spec(name, matrices[name], directions[name])
             for name in FACTOR_NAMES}

    evaluations: dict[str, dict] = {}
    for horizon in horizons:
        evaluations[str(horizon)] = {}
        for window, (start, end) in WINDOWS.items():
            t0 = time.time()
            report = fl.evaluate_panel(
                panel,
                factor_names=list(FACTOR_NAMES),
                horizon=int(horizon),
                cost=0.003,
                start=start,
                end=end,
                factor_specs=specs,
                min_cross_section=MIN_N,
                n_boot=int(n_boot),
            )
            evaluations[str(horizon)][window] = {
                "start": start,
                "end": end,
                "runtime_sec": round(time.time() - t0, 3),
                "effective": report["meta"].get("effective"),
                "baseline": report["meta"].get("baseline"),
                "factors": {name: _extract(report, name) for name in FACTOR_NAMES},
            }

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "panel": {
            "start": PANEL_START,
            "end": PANEL_END,
            "shape": list(panel.shape),
            "first_date": str(dates[0]),
            "last_date": str(dates[-1]),
            "eligible_mean": float(eligible.to_numpy(dtype=float).mean()),
        },
        "definitions": {
            "b1_mom_21d": "adj_close/adj_close.shift(21)-1，direction=-1",
            "return_21d": "factor_lib_tushare return_21d，direction=-1",
            "p1_mom_sleeve": f"P1 动量族 {len(mom_names)} 因子等权 directed rank",
            "momentum_names": mom_names,
            "orthogonalization": "逐日截面对 directed rank 做 OLS（含截距），取残差；残差方向 +1",
        },
        "diagnostics": {
            "raw_exact_equal": raw_exact,
            "raw_max_abs_diff": float(raw_diff.where(eligible).max().max()),
            "rank_corr_b1_vs_return21": rank_corr,
            "b1_sleeve_resid_var_ratio": var_ratio,
            "resid_vs_return21": diag_r21,
            "resid_vs_sleeve": diag_sleeve,
        },
        "evaluations": evaluations,
    }


def _fmt_ic(entry: dict) -> str:
    if not entry:
        return "n/a"
    ic = entry.get("ic") or {}
    d = entry.get("ic_directed") or {}
    mean = d.get("mean")
    t = d.get("t")
    if mean is None:
        return f"n={entry.get('n_days', 0)}（无有效 IC）"
    return f"{mean:+.4f} (t={t:+.2f}, n={d.get('n_days', 0)})"


def write_report(result: dict, out_md: Path) -> None:
    diag = result["diagnostics"]
    corr = diag["rank_corr_b1_vs_return21"]
    lines = [
        "# b1_mom_21d 截面正交化后的独立 IC 检验",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 面板：{result['panel']['start']}–{result['panel']['end']}，"
        f"{result['panel']['shape'][0]} 日 × {result['panel']['shape'][1]} 股",
        f"- 因子：b1_mom_21d（21 日反转）、return_21d（P1 已有）、"
        f"P1 {len(result['definitions']['momentum_names'])} 因子动量 sleeve",
        "",
        "## 1. 结论",
        "",
    ]
    if diag["raw_exact_equal"]:
        lines.append("- b1_mom_21d 与 return_21d 的原始值**完全相等**（最大绝对差 0），"
                     "不是两个独立因子。")
    else:
        lines.append(f"- b1_mom_21d 与 return_21d 原始最大绝对差 {diag['raw_max_abs_diff']:.3e}。")
    lines.append(f"- 两者截面秩相关：mean {corr['mean']:.4f}、median {corr['median']:.4f}"
                 f"（{corr['n_days']} 日，min {corr['min']:.4f} / max {corr['max']:.4f}）。")
    vr = diag["b1_sleeve_resid_var_ratio"]
    lines.append(f"- 对 P1 动量 sleeve 正交化后残差方差占原信号方差："
                 f"mean {vr['mean']:.4f}、median {vr['median']:.4f}"
                 f"（min {vr['min']:.4f} / max {vr['max']:.4f}）。")
    if diag["resid_vs_return21"].get("degenerate"):
        lines.append("- **对 return_21d 的残差恒为 0**（最大绝对残差 <1e-12），"
                     "独立 IC 无定义；不能把浮点排序噪声当成 IC。")
    lines.append("")
    lines.append("## 2. 分窗口 IC")
    lines.append("")
    for horizon in HORIZONS:
        lines.append(f"### {horizon} 日 forward CAR（30bp）")
        lines.append("")
        lines.append("| 因子 | dev | holdout | full |")
        lines.append("|---|---|---|---|")
        for name in FACTOR_NAMES:
            row = [name]
            for window in ("dev", "holdout", "full"):
                entry = result["evaluations"][str(horizon)][window]["factors"].get(name)
                if name == "b1_mom21_resid_vs_return21" and diag["resid_vs_return21"].get("degenerate"):
                    row.append("残差恒为 0（无定义）")
                else:
                    row.append(_fmt_ic(entry))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    lines.append("## 3. 解释")
    lines.append("")
    lines.append("- 所有因子矩阵均为 directed rank，IC 正值表示反转信号方向正确；"
                 "t 值为日度 IC 序列的 t 统计量。")
    if diag["raw_exact_equal"]:
        lines.append("- b1_mom_21d 与 return_21d 原始值完全相等，因此对 return_21d 的残差恒为 0，"
                     "不存在“超出 return_21d 的独立 IC”。")
    else:
        lines.append(f"- b1_mom_21d 与 return_21d 原始最大绝对差 {diag['raw_max_abs_diff']:.3e}，"
                     "两者并非同源。")
    lines.append("- 对 P1 动量 sleeve 的残差仍有非零 IC，来源是 b1_mom_21d 与 sleeve 内"
                 "其他动量因子的差异；它等价于对 sleeve 里已有 return_21d 的重新加权，"
                 "不是新 alpha。")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="20,60")
    ap.add_argument("--n-boot", type=int, default=100)
    ap.add_argument("--out-json", default=str(ROOT / "research" / "reports"
                                            / "b1_mom21_residual_ic_20261106.json"))
    ap.add_argument("--out-md", default=str(ROOT / "deliverables"
                                           / "2026-11-06_B1_mom21残差独立IC检验.md"))
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())

    t0 = time.time()
    print("[load] panel ...", flush=True)
    panel = fl.load_panel(PANEL_START, PANEL_END, horizon=20,
                          universe="tradable", warmup=140, load_roe=False)
    print(f"[load] panel {panel.shape} in {time.time() - t0:.1f}s", flush=True)
    result = build(panel, horizons=horizons, n_boot=int(args.n_boot))
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(result, out_md)
    print(f"[done] {out_json} ({out_json.stat().st_size}B), {out_md}", flush=True)
    print(f"[done] runtime {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
