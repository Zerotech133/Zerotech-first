# -*- coding: utf-8 -*-
"""W2-6 四关前筛：8 个学习因子的 RankIC/分层/衰减/PIT（司马微 · task-150）。

不代替正式四关，只做**前筛**：
  样本：真实日线最近 120 个交易日 × 300 只（确定性抽样 seed=42，含 130 日 warmup 供滚动窗口）；
  PIT：t 日因子只用 t 及以前数据；未来收益 1/5/10/20 日（复权价 close × adj_factor）；
  输出：RankIC 均值/IR、5 层分层多空/单调性、衰减曲线、覆盖率、PIT 检查；
  结论分三级：通过 / 观察 / 不通过（阈值见 THRESHOLDS，可解释、先定后用）；
  post_limit_behavior 使用未来 N 日数据（非 PIT），单列描述、**不参与 IC/分层**（判例 H29）。

产出：research/reports/factor_prescreen_<tag>.{json,md}（默认 tag=运行日；--tag/--out-dir 可覆盖）
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/factor_prescreen.py
约束：只读数据根；不改策略/因子实现；不 commit。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "quant_backtest" / "factor_lib"))

import learning_factors as lf  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
N_DAYS, N_CODES, WARMUP, SEED = 120, 300, 130, 42
HORIZONS = (1, 5, 10, 20)
PRIMARY_H = 20
MIN_NAMES = 30              # 单日 IC / 分层最少有效股票数
LAYERS = 5
TRUNCATION_TOL = 1e-9

# 前筛阈值（先定后用；只说「前筛通过」，不代替正式四关）
THRESHOLDS = {
    "primary_horizon": PRIMARY_H,
    "abs_ic_min": 0.02,          # |directed RankIC| ≥ 0.02
    "ir_min": 0.3,               # |IR| = |mean IC| / std IC ≥ 0.3
    "coverage_min_pct": 60.0,    # 因子覆盖率
    "split_half_same_sign": True,  # 前半窗/后半窗 directed IC 同号
    "pit_required": True,        # PIT 截断不变性必须通过
    "grade_rules": {
        "通过": "PIT 通过 且 覆盖率≥60% 且 |IC(20d)|≥0.02 且 |IR(20d)|≥0.3 且 前后半窗同号"
                "（方向先验不符则降级为观察）",
        "观察": "满足 |IC|≥0.02 或 |IR|≥0.3 的任一项，或仅在短周期(1/5/10d)显著，但未全部满足通过条件",
        "不通过": "|IC|<0.02 且 |IR|<0.3（或数据不可用/方向先验相反且无短周期信号）",
    },
}
# 8 因子：7 个 PIT 安全因子进入 IC；post_limit_behavior 单列
PIT_FACTORS = ["ma_convergence", "bull_alignment_strength", "macd_divergence_strength",
               "volume_breakout_strength", "platform_consolidation_score",
               "chip_concentration", "moneyflow_strength"]
NON_PIT = ["post_limit_behavior"]


# ============================================================ 纯函数（可单测）
def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    """一维 Spearman（秩相关）；样本不足或零方差 → None。"""
    m = np.isfinite(a) & np.isfinite(b)
    if int(m.sum()) < 3:
        return None
    x, y = a[m], b[m]
    if np.std(x) <= 0 or np.std(y) <= 0:
        return None
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def rank_ic(fmat: np.ndarray, label: np.ndarray, min_names: int = MIN_NAMES) -> pd.Series:
    """逐日横截面 RankIC（Spearman）序列，日期索引 0..D-1。"""
    ics = []
    for i in range(fmat.shape[0]):
        ic = _spearman(fmat[i], label[i])
        m = np.isfinite(fmat[i]) & np.isfinite(label[i])
        ics.append(ic if (ic is not None and int(m.sum()) >= min_names) else np.nan)
    return pd.Series(ics, dtype=float)


def ic_stats(ic: pd.Series, direction: int = 0) -> dict:
    """RankIC 统计：均值/IR/t/胜率/半窗稳定性；directed = direction × IC（direction=0 不翻转）。"""
    s = pd.Series(ic, dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "ic_mean": None, "ic_std": None, "ir": None, "t": None,
                "positive_rate": None, "directed_ic_mean": None, "directed_ir": None,
                "half_split": {"first_mean": None, "second_mean": None, "same_sign": None}}
    mean, std = float(s.mean()), (float(s.std(ddof=1)) if n > 1 else 0.0)
    ir = mean / std if std > 0 else None
    t = ir * math.sqrt(n) if ir is not None else None
    half = n // 2
    f_mean = float(s.iloc[:half].mean()) if half else None
    s_mean = float(s.iloc[half:].mean()) if n - half else None
    d = float(direction) if direction in (1, -1) else 1.0
    def _sign(x):
        return None if x is None else (1 if x > 0 else (-1 if x < 0 else 0))
    same = None
    if f_mean is not None and s_mean is not None:
        same = _sign(f_mean * d) == _sign(s_mean * d)
    return {"n_days": n, "ic_mean": lf._num(mean, 6), "ic_std": lf._num(std, 6),
            "ir": lf._num(ir, 3), "t": lf._num(t, 3),
            "positive_rate": lf._num(float((s > 0).mean()), 4),
            "directed_ic_mean": lf._num(mean * d, 6), "directed_ir": lf._num(ir * d, 3) if ir else None,
            "half_split": {"first_mean": lf._num(f_mean, 6),
                           "second_mean": lf._num(s_mean, 6), "same_sign": same}}


def layer_stats(fmat: np.ndarray, label: np.ndarray, horizon: int,
                n_layers: int = LAYERS, min_names: int = MIN_NAMES,
                direction: int = 0) -> dict:
    """5 层分层：逐日按因子分位分组，组内未来 h 日收益均值；多空 = G5−G1（方向调整）。"""
    d = fmat.shape[0]
    daily: dict[int, list[float]] = {g: [] for g in range(1, n_layers + 1)}
    n_used = 0
    for i in range(d):
        valid = np.isfinite(fmat[i]) & np.isfinite(label[i])
        if int(valid.sum()) < min_names:
            continue
        n_used += 1
        x = fmat[i][valid]
        y = label[i][valid]
        n = len(x)
        # 序数分桶（稳定排序破并列），保证 5 组样本量均衡、不因大量并列出现空组
        order = np.argsort(x, kind="stable")
        pos = np.empty(n, dtype=float)
        pos[order] = np.arange(n)
        gidx = np.clip(np.floor(pos / n * n_layers).astype(int) + 1, 1, n_layers)
        for g in range(1, n_layers + 1):
            sel = gidx == g
            if int(sel.sum()) == 0:
                continue
            daily[g].append(float(np.mean(y[sel])))
    groups = {}
    means = []
    for g in range(1, n_layers + 1):
        arr = np.asarray(daily[g], dtype=float)
        m = float(arr.mean()) if len(arr) else None
        means.append(m)
        groups[f"G{g}"] = {"n_days": int(len(arr)),
                           "mean_fwd_pct": lf._num(m, 4)}
    ls = None
    if means[0] is not None and means[-1] is not None:
        ls_mean_pct = (means[-1] - means[0]) * (1 if direction == 0 else direction)
        ls = {"mean_fwd_pct": lf._num(ls_mean_pct, 4),
              "ann_pct": lf._num(ls_mean_pct * 252.0 / horizon, 2)}
    mono = None
    if all(m is not None for m in means) and len(set(means)) > 1:
        mono = float(pd.Series(means).corr(pd.Series(np.arange(1, n_layers + 1)),
                                           method="spearman"))
        if direction != 0:
            mono = mono * direction
    return {"n_days": n_used, "groups": groups,
            "long_short": ls, "monotonicity": lf._num(mono, 3),
            "note": "组内等权未来 h 日收益均值；多空=G5−G1；单调性=层序号与收益的 Spearman"}


def grade_factor(ic_by_h: dict, coverage_pct: float, pit_pass: bool,
                 direction: int = 0) -> tuple[str, list[str]]:
    """结论分级（阈值先定后用）：通过/观察/不通过 + 理由。"""
    reasons = []
    h = PRIMARY_H
    st = ic_by_h.get(f"h{h}") or {}
    ic, ir = st.get("directed_ic_mean"), st.get("directed_ir")
    same = (st.get("half_split") or {}).get("same_sign")
    short_signal = False
    for hh in (1, 5, 10):
        s2 = ic_by_h.get(f"h{hh}") or {}
        if s2.get("directed_ic_mean") is not None and abs(s2["directed_ic_mean"]) >= THRESHOLDS["abs_ic_min"]:
            short_signal = True
    if not pit_pass:
        return "不通过", ["PIT 截断不变性未通过：因子存在未来数据泄漏，禁止使用"]
    if coverage_pct is not None and coverage_pct < THRESHOLDS["coverage_min_pct"]:
        return "不通过", [f"覆盖率 {coverage_pct}% < {THRESHOLDS['coverage_min_pct']}%（数据缺口过大）"]
    abs_ic = None if ic is None else abs(ic)
    abs_ir = None if ir is None else abs(ir)
    ok_ic = abs_ic is not None and abs_ic >= THRESHOLDS["abs_ic_min"]
    ok_ir = abs_ir is not None and abs_ir >= THRESHOLDS["ir_min"]
    if ic is None:
        return "不通过", ["IC(20d) 无有效样本"]
    if direction != 0 and ic < 0:
        if abs_ic >= THRESHOLDS["abs_ic_min"]:
            reasons.append(f"方向与先验相反（directed IC={ic} <0）：按先验不得通过，"
                           f"若反向研究需单独立项验证（不得直接上策略）")
            if short_signal:
                reasons.append("短周期(1/5/10d)同向且幅度可观 → 反向信号值得做正式四关前的专项复核")
            return "观察", reasons
        return "不通过", [f"|IC(20d)|={abs_ic} 低于 {THRESHOLDS['abs_ic_min']} 且方向与先验相反，"
                          f"无短周期正向信号"]
    if ok_ic and ok_ir and (same is not False):
        return "通过", [f"|IC(20d)|={abs_ic:.4f}≥{THRESHOLDS['abs_ic_min']}、"
                        f"|IR|={abs_ir:.2f}≥{THRESHOLDS['ir_min']}、前后半窗同号={same}、PIT 通过"]
    if ok_ic or ok_ir or short_signal:
        reasons.append(f"IC={ic}、IR={ir}、同号={same}、短周期有信号={short_signal}："
                       f"未同时满足『通过』全部门槛")
        return "观察", reasons
    return "不通过", [f"|IC(20d)|={abs_ic}、|IR|={abs_ir} 均低于门槛（且短周期无信号）"]


def pit_truncation_check(factor_fn, sample: dict, factor_names, t_date: str) -> dict:
    """PIT 截断不变性：把样本截断到 t_date 后重算因子，t_date 当日因子值应与全样本一致。"""
    trunc = {
        "fields": {k: v.loc[v.index <= t_date] for k, v in sample["fields"].items()},
        "window_dates": [d for d in sample["window_dates"] if d <= t_date],
        "all_dates": [d for d in sample["all_dates"] if d <= t_date],
        "codes": list(sample["codes"]),
        "data_end": t_date,
        "moneyflow": sample["moneyflow"][sample["moneyflow"]["trade_date"] <= t_date]
        if len(sample.get("moneyflow")) else sample.get("moneyflow"),
        "limit_up": sample["limit_up"][sample["limit_up"]["trade_date"] <= t_date]
        if len(sample.get("limit_up")) else sample.get("limit_up"),
        "cyq": sample["cyq"][sample["cyq"]["trade_date"] <= t_date]
        if len(sample.get("cyq")) else sample.get("cyq"),
    }
    full = factor_fn(sample)["matrices"]
    part = factor_fn(trunc)["matrices"]
    out = {}
    for name in factor_names:
        if name not in full or name not in part:
            out[name] = {"pass": None, "max_abs_diff": None, "reason": "因子矩阵缺失"}
            continue
        a = full[name]
        b = part[name]
        if t_date not in a.index or t_date not in b.index:
            out[name] = {"pass": None, "max_abs_diff": None, "reason": "截断日不在窗口内"}
            continue
        va = pd.Series(a.loc[t_date], dtype=float)
        vb = pd.Series(b.loc[t_date], dtype=float).reindex(va.index)
        both = va.notna() & vb.notna()
        diff = float((va[both] - vb[both]).abs().max()) if int(both.sum()) else None
        nan_mismatch = int((va.notna() != vb.notna()).sum())
        ok = diff is not None and diff <= TRUNCATION_TOL and nan_mismatch == 0
        out[name] = {"pass": bool(ok), "max_abs_diff": diff,
                     "n_compare": int(both.sum()), "nan_mismatch": nan_mismatch,
                     "truncation_date": t_date}
    return out


def validate_report_schema(rep: dict) -> list[str]:
    """报告 schema 自检（空=通过）。"""
    errors = []
    for k in ("schema_version", "meta", "thresholds", "pit_check", "factors",
              "post_limit_behavior", "summary"):
        if k not in rep:
            errors.append(f"缺顶层字段: {k}")
    if errors:
        return errors
    for name in PIT_FACTORS:
        f = rep["factors"].get(name)
        if not f:
            errors.append(f"缺因子: {name}")
            continue
        for k in ("ic", "layers", "decay", "coverage", "grade", "pit"):
            if k not in f:
                errors.append(f"{name} 缺字段: {k}")
        if f.get("grade") not in ("通过", "观察", "不通过"):
            errors.append(f"{name} grade 非法: {f.get('grade')}")
        for h in HORIZONS:
            if f"h{h}" not in (f.get("ic") or {}):
                errors.append(f"{name}.ic 缺 h{h}")
    for k in ("n_events", "ret_n_mean", "max_drawdown_n_mean", "positive_rate",
              "excluded_from_ic"):
        if k not in (rep.get("post_limit_behavior") or {}):
            errors.append(f"post_limit_behavior 缺字段: {k}")
    if rep["post_limit_behavior"].get("excluded_from_ic") is not True:
        errors.append("post_limit_behavior 必须标记 excluded_from_ic=True")
    total = sum(len(rep["summary"].get(k, [])) for k in ("通过", "观察", "不通过"))
    if total != len(PIT_FACTORS):
        errors.append(f"summary 分级数 {total} != PIT 因子数 {len(PIT_FACTORS)}")
    return errors


# ============================================================ 数据
def load_adj_close(sample: dict) -> pd.DataFrame:
    """复权收盘价矩阵（close × adj_factor，PIT 安全：只用当日及以前）。"""
    root = Path(sample["data_root"])
    dates = list(sample["all_dates"])
    codes = set(sample["codes"])
    frames = []
    for dt in dates:
        path = root / "adj_factor" / f"d_{dt}.parquet"
        if not path.exists():
            continue
        df = pq.read_table(path, columns=["ts_code", "adj_factor"]).to_pandas()
        df = df[df["ts_code"].isin(codes)]
        if len(df):
            df["trade_date"] = str(dt)
            frames.append(df)
    adj = (pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
           .pivot(index="trade_date", columns="ts_code", values="adj_factor")
           .reindex(index=dates, columns=list(sample["codes"])))
    return sample["fields"]["close"].reindex(index=dates, columns=list(sample["codes"])) * adj


def forward_returns(adj_close: pd.DataFrame, window_dates: list[str]) -> dict[int, np.ndarray]:
    """未来 h 日收益矩阵（窗口内日期 × 代码）：fwd_h[t] = adj[t+h]/adj[t] - 1。"""
    idx = {d: i for i, d in enumerate(adj_close.index)}
    rows = [idx[d] for d in window_dates if d in idx]
    out = {}
    arr = adj_close.to_numpy(dtype=float)
    for h in HORIZONS:
        mat = np.full((len(rows), arr.shape[1]), np.nan)
        for k, i in enumerate(rows):
            if i + h < arr.shape[0]:
                with np.errstate(invalid="ignore", divide="ignore"):
                    mat[k] = arr[i + h] / arr[i] - 1.0
        out[h] = mat * 100.0  # 百分比
    return out


# ============================================================ 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="学习因子四关前筛（动态输出名）")
    ap.add_argument("--tag", default="", help="输出名后缀，默认运行日 YYYYMMDD")
    ap.add_argument("--out-dir", default="", help="输出目录，默认 research/reports")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else OUT
    tag = args.tag or datetime.now().strftime("%Y%m%d")
    stem = f"factor_prescreen_{tag}"
    t0 = time.time()
    sample = lf.load_sample_data(n_days=N_DAYS, n_codes=N_CODES, warmup=WARMUP, seed=SEED)
    window_dates = list(sample["window_dates"])
    codes = list(sample["codes"])
    print(f"[prescreen] 样本加载 {time.time()-t0:.1f}s：{len(window_dates)} 日 × {len(codes)} 只",
          flush=True)
    adj = load_adj_close(sample)
    fwd = forward_returns(adj, window_dates)
    computed = lf.compute_factor_frame(sample)
    matrices = computed["matrices"]
    behavior = computed["post_limit_behavior"]
    print(f"[prescreen] 因子计算 + 前向收益就绪 {time.time()-t0:.1f}s", flush=True)

    # ---- PIT 检查（静态 + 动态截断不变性）
    t_check = window_dates[-11] if len(window_dates) > 12 else window_dates[-1]
    trunc = pit_truncation_check(lf.compute_factor_frame, sample, PIT_FACTORS, t_check)
    pit_static = [{"factor": n, "pit_safe": lf.LEARNING_FACTOR_SPECS[n].pit_safe,
                   "deps": list(lf.LEARNING_FACTOR_SPECS[n].data_deps),
                   "truncation": trunc.get(n)} for n in PIT_FACTORS]
    pit_static.append({"factor": "post_limit_behavior", "pit_safe": False,
                       "deps": list(lf.LEARNING_FACTOR_SPECS["post_limit_behavior"].data_deps),
                       "note": "使用未来 N 日数据（事后描述），不参与 IC/分层，单列"})
    pit_all_pass = all((trunc.get(n) or {}).get("pass") for n in PIT_FACTORS)
    print(f"[prescreen] PIT 截断检查 {t_check}：all_pass={pit_all_pass}", flush=True)

    # ---- 逐因子 IC / 分层 / 衰减 / 覆盖率
    factors_payload = {}
    for name in PIT_FACTORS:
        spec = lf.LEARNING_FACTOR_SPECS[name]
        mat = np.asarray(matrices[name], dtype=float) if name in matrices else None
        coverage = None
        data_gaps = []
        if mat is None:
            data_gaps.append("因子矩阵缺失")
            ic_by_h, layers, decay = {}, {}, {}
        else:
            vals = mat[np.isfinite(mat)]
            coverage = round(100.0 * vals.size / mat.size, 2) if mat.size else 0.0
            if name == "chip_concentration":
                n_codes_cov = int((np.isfinite(mat).sum(axis=0) > 0).sum())
                data_gaps.append(f"cyq_chips 数据仓覆盖受限：{n_codes_cov}/{len(codes)} 只代码有数据")
            if name == "moneyflow_strength" and coverage < 60:
                data_gaps.append("moneyflow 数据覆盖不足")
            ic_by_h, layers, decay = {}, {}, {}
            for h in HORIZONS:
                ic = rank_ic(mat, fwd[h])
                st = ic_stats(ic, direction=spec.direction)
                ic_by_h[f"h{h}"] = st
                decay[f"h{h}"] = st.get("directed_ic_mean")
                layers[f"h{h}"] = layer_stats(mat, fwd[h], h, direction=spec.direction)
        pit_info = trunc.get(name) or {}
        grade, reasons = grade_factor(ic_by_h, coverage, bool(pit_info.get("pass")),
                                      direction=spec.direction)
        factors_payload[name] = {
            "name": name, "title": spec.title, "formula": spec.formula,
            "direction_prior": spec.direction, "pit_safe": spec.pit_safe,
            "data_deps": list(spec.data_deps),
            "coverage": {"valid": int(np.isfinite(mat).sum()) if mat is not None else 0,
                         "expected": int(mat.size) if mat is not None else 0,
                         "pct": coverage},
            "data_gaps": data_gaps,
            "ic": ic_by_h, "decay": decay, "layers": layers,
            "pit": {"static_safe": spec.pit_safe, "truncation": pit_info},
            "grade": grade, "grade_reasons": reasons,
            "next_action": {"通过": "进入正式四关（行业/市值中性化、成本 30/50/80bp、容量/覆盖）",
                            "观察": "补数据或补口径后复筛；方向先验相反者仅可做反向因子专项研究，"
                                    "不得直接上策略",
                            "不通过": "暂不投入；若属数据缺口（如 cyq/moneyflow）可待覆盖补齐后再复筛"}
            .get(grade, ""),
        }
        print(f"[prescreen] {name}: coverage={coverage}% grade={grade} "
              f"IC20={ic_by_h.get('h20', {}).get('directed_ic_mean')} "
              f"IR20={ic_by_h.get('h20', {}).get('directed_ir')}", flush=True)

    # ---- post_limit_behavior 单列（不参与 IC）
    if isinstance(behavior, pd.DataFrame) and len(behavior):
        beh = behavior.copy()
        beh["ret_n"] = pd.to_numeric(beh["ret_n"], errors="coerce")
        beh["max_drawdown_n"] = pd.to_numeric(beh["max_drawdown_n"], errors="coerce")
        beh["next_day_ret"] = pd.to_numeric(beh["next_day_ret"], errors="coerce")
        behavior_payload = {
            "n_events": int(len(beh)),
            "n_codes": int(beh["ts_code"].nunique()),
            "ret_n_mean": lf._num(float(beh["ret_n"].mean()), 4),
            "ret_n_median": lf._num(float(beh["ret_n"].median()), 4),
            "max_drawdown_n_mean": lf._num(float(beh["max_drawdown_n"].mean()), 4),
            "positive_rate": lf._num(float((beh["ret_n"] > 0).mean()), 4),
            "next_day_ret_mean": lf._num(float(beh["next_day_ret"].mean()), 4),
            "excluded_from_ic": True,
            "reason": "使用未来 N 日数据（非 PIT），仅作学习标签/行为描述，禁止作为交易信号（判例 H29）",
        }
    else:
        behavior_payload = {"n_events": 0, "ret_n_mean": None, "max_drawdown_n_mean": None,
                            "positive_rate": None, "next_day_ret_mean": None,
                            "excluded_from_ic": True,
                            "reason": "窗口内无涨停事件（limit_list_d 无 U 或样本覆盖不足），数据缺口已记录"}

    rep = {
        "schema_version": "factor_prescreen.v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "researcher": "司马微", "task": "task-150 W2-6 四关前筛（不代替正式四关）",
            "source": "quant_backtest/factor_lib/learning_factors.py",
            "sample": {"n_days": len(window_dates), "n_codes": len(codes),
                       "window_start": window_dates[0], "window_end": window_dates[-1],
                       "warmup_days": WARMUP, "seed": SEED, "data_end": sample["data_end"]},
            "horizons": list(HORIZONS), "primary_horizon": PRIMARY_H,
            "return_label": "复权价 close×adj_factor；fwd_h = adj[t+h]/adj[t] − 1（%）；"
                            "T 日收盘因子 → T+h 收盘（不做 T+1 入场，前筛口径）",
            "data_root": sample["data_root"],
            "sources": sample.get("sources", {}),
            "runtime_sec": None,
        },
        "thresholds": THRESHOLDS,
        "pit_check": {
            "dynamic": pit_static,
            "all_pit_factors_pass": bool(pit_all_pass),
            "truncation_date": t_check,
            "method": "把样本截断到 t_date 重算因子，t_date 当日值与全样本比较（diff≤1e-9 且 NaN 模式一致）",
            "note": "post_limit_behavior 使用未来数据，按非 PIT 单列，不参与 IC",
        },
        "factors": factors_payload,
        "post_limit_behavior": behavior_payload,
        "summary": {},
        "limitations": [
            "前筛样本仅最近 120 交易日 × 300 只（抽样 seed=42），非全市场长样本，结论只作「是否值得进正式四关」参考；",
            "收益标签为 T→T+h 收盘（未做 T+1 入场/涨跌停/停牌/成本），短窗口 h=20 尾部样本天数减少；",
            "chip_concentration/moneyflow 数据仓覆盖有限，覆盖率不足的因子按不通过/观察处理；",
            "post_limit_behavior 非 PIT，单列描述、禁止作为交易信号；",
            "行业/市值中性化未在本前筛做（正式四关再做）；不构成投资建议。",
        ],
    }
    # 汇总
    summary = {"通过": [], "观察": [], "不通过": []}
    for name, f in factors_payload.items():
        summary[f["grade"]].append(name)
    rep["summary"] = summary
    rep["schema_check"] = validate_report_schema(rep)
    rep["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    if rep["schema_check"]:
        print(f"[prescreen] WARN schema: {rep['schema_check']}", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- MD
    L = [
        "# 学习因子四关前筛（W2-6 · 司马微）", "",
        f"- 生成：{rep['meta']['generated_at']}；样本 **{len(window_dates)} 个交易日 × {len(codes)} 只**"
        f"（{window_dates[0]}~{window_dates[-1]}，warmup {WARMUP} 日，seed={SEED}）",
        "- 来源：`quant_backtest/factor_lib/learning_factors.py`（8 个学习因子）；只做前筛，不代替正式四关",
        "- 收益标签：复权价 close×adj_factor，fwd_h = adj[t+h]/adj[t] − 1（1/5/10/20 日，T 收盘→T+h 收盘）",
        f"- 运行时：{rep['meta']['runtime_sec']}s；数据根只读", "",
        "## 一、前筛阈值（先定后用）", "",
        f"- **通过**：{THRESHOLDS['grade_rules']['通过']}",
        f"- **观察**：{THRESHOLDS['grade_rules']['观察']}",
        f"- **不通过**：{THRESHOLDS['grade_rules']['不通过']}",
        "", "## 二、汇总（7 个 PIT 因子；post_limit_behavior 不参与）", "",
        "| 因子 | 覆盖率 | IC(20d,方向) | IR(20d) | 半窗同号 | 长空(20d,年化) | 单调性 | 分级 |",
        "|---|---|---|---|---|---|---|---|"]
    for name, f in factors_payload.items():
        st = f["ic"].get("h20") or {}
        lay = (f["layers"].get("h20") or {})
        ls = (lay.get("long_short") or {}).get("ann_pct")
        L.append("| {} | {}% | {} | {} | {} | {} | {} | **{}** |".format(
            name, f["coverage"]["pct"], st.get("directed_ic_mean"), st.get("directed_ir"),
            (st.get("half_split") or {}).get("same_sign"),
            "—" if ls is None else f"{ls}%", "—" if lay.get("monotonicity") is None
            else lay.get("monotonicity"), f["grade"]))
    L += ["", "### 衰减曲线（按 horizon 的 directed RankIC 均值）", "",
          "| 因子 | 1d | 5d | 10d | 20d |", "|---|---|---|---|---|"]
    for name, f in factors_payload.items():
        L.append("| {} | {} | {} | {} | {} |".format(
            name, f["decay"].get("h1"), f["decay"].get("h5"),
            f["decay"].get("h10"), f["decay"].get("h20")))
    L += ["", "## 三、PIT 检查", "",
          f"- 动态截断不变性（截断日 {t_check}）：7 个 PIT 因子全部通过 = **{pit_all_pass}**"]
    for item in pit_static:
        tr = item.get("truncation") or {}
        if item["factor"] in PIT_FACTORS:
            L.append(f"  - {item['factor']}: pass={tr.get('pass')}，"
                     f"max|diff|={tr.get('max_abs_diff')}，比较 {tr.get('n_compare')} 个值")
    L += ["- post_limit_behavior：**非 PIT**（使用未来 N 日数据），按协议单列、不参与 IC/分层。", "",
          "## 四、涨停后行为（单列，非 PIT，禁止作为交易信号）", ""]
    b = rep["post_limit_behavior"]
    L += [f"- 事件数：{b['n_events']}（{b.get('n_codes', 0)} 只代码）；ret_n 均值 {b['ret_n_mean']}、"
          f"中位 {b.get('ret_n_median')}；N 日最大回落均值 {b['max_drawdown_n_mean']}；"
          f"正收益比例 {b['positive_rate']}；next_day_ret 均值 {b.get('next_day_ret_mean')}",
          f"- 说明：{b['reason']}", "",
          "## 五、逐因子结论", ""]
    for name, f in factors_payload.items():
        L.append(f"### {name}（{f['title']}）— **{f['grade']}**")
        L.append("")
        for r in f["grade_reasons"]:
            L.append(f"- {r}")
        lay20 = f["layers"].get("h20") or {}
        lsl = (lay20.get("long_short") or {})
        ls_ann = lsl.get("ann_pct")
        mono20 = lay20.get("monotonicity")
        L.append(f"- 覆盖率 {f['coverage']['pct']}%（{f['coverage']['valid']}/{f['coverage']['expected']}）；"
                 f"方向先验 {f['direction_prior']:+d}；PIT={f['pit']['static_safe']}")
        L.append("- 5 层（20d）：多空年化 " + ("—" if ls_ann is None else f"{ls_ann}%")
                 + "、单调性 " + ("—" if mono20 is None else str(mono20))
                 + f"；衰减 directed IC = 1d {f['decay'].get('h1')} / 5d {f['decay'].get('h5')} / "
                 f"10d {f['decay'].get('h10')} / 20d {f['decay'].get('h20')}")
        for gap in f.get("data_gaps", []):
            L.append(f"- 数据缺口：{gap}")
        L.append(f"- 建议：{f.get('next_action', '')}")
        L.append("")
    L += ["## 六、局限", ""] + [f"- {x}" for x in rep["limitations"]]
    (out_dir / f"{stem}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "schema_check": rep["schema_check"],
                      "pit_pass": pit_all_pass,
                      "runtime": rep["meta"]["runtime_sec"]}, ensure_ascii=False), flush=True)
    return 0 if not rep["schema_check"] else 1


if __name__ == "__main__":
    sys.exit(main())
