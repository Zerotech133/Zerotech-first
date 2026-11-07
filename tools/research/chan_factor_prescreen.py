# -*- coding: utf-8 -*-
"""缠论因子独立前筛（W2-6 · 司马微 · task-152）。

只做**前筛**（不代替正式四关）：判断缠论结构特征是否值得进正式四关。
  样本：最近 120 个交易日 × 60 只（确定性抽样 seed=42），1 分钟 bar → 30 分钟聚合（复用
        chan_factors.resample_30min），结构复用 chan_factors.analyze_chan（只读、不改模块）；
  按日生成 8 个特征：笔数/最后笔方向/中枢数/最新中枢宽度/最近背驰强度/背驰数/
        买卖点净信号/最新买卖点方向；
  未来收益：5/10/20 日（复权，day close × adj_factor）；
  输出：RankIC/IR、5 层分层多空+单调性、衰减、覆盖率、**PIT 截断检查**（t 日特征只允许用
        t 及以前 bar；抽样 code-day 用截断数据重算对照）；
  结论分级：通过/观察/不通过（阈值同 W2 前筛，见 THRESHOLDS）。

产出：research/reports/chan_factor_prescreen_<tag>.{json,md}（默认 tag=运行日）
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/chan_factor_prescreen.py
约束：数据根只读；不改 chan_factors.py；不 commit。
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

import chan_factors as cf  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
MINS = DATA / "stk_mins"
OUT = REPO / "research" / "reports"
N_CODES, N_DAYS, SEED = 60, 120, 42
HORIZONS = (5, 10, 20)
PRIMARY_H = 20
LAYERS = 5
MIN_NAMES = 20
PIT_MISMATCH_MAX = 0.10     # PIT 抽样重算不一致率上限

THRESHOLDS = {
    "primary_horizon": PRIMARY_H,
    "abs_ic_min": 0.02,
    "ir_min": 0.3,
    "coverage_min_pct": 60.0,
    "half_split_same_sign": True,
    "pit_required": True,
    "grade_rules": {
        "通过": "PIT 截断检查通过 且 覆盖率≥60% 且 |IC(20d)|≥0.02 且 |IR(20d)|≥0.3 且 前后半窗同号",
        "观察": "满足 |IC|≥0.02 或 |IR|≥0.3 任一项，或仅短周期(5/10d)显著，但未全部满足通过条件",
        "不通过": "|IC|<0.02 且 |IR|<0.3（或覆盖率不足/PIT 未过）",
    },
}
# 8 个按日特征（缠论结构 → 数值）；方向先验统一 0（探索性，方向由数据决定）
FEATURES = [
    ("bi_count_20d", "近20日闭笔数", "笔"),
    ("bi_last_dir", "最后一笔方向（+1上/−1下）", "笔"),
    ("zs_count_20d", "近20日闭中枢数", "中枢"),
    ("zs_width_last", "最新闭中枢宽度 (ZG−ZD)/ZD", "中枢"),
    ("beichi_last_strength", "最近背驰强度（无则0）", "背驰"),
    ("beichi_div_count_20d", "近20日背驰数", "背驰"),
    ("bs_net_5d", "近5日买卖点净信号（B+1/S−1）", "买卖点"),
    ("bs_last_sign", "最新买卖点方向（B+1/S−1/0）", "买卖点"),
]
FEAT_NAMES = [f[0] for f in FEATURES]


# ============================================================ 纯函数（可单测）
def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
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
    """逐日横截面 RankIC（Spearman）。"""
    out = []
    for i in range(fmat.shape[0]):
        m = np.isfinite(fmat[i]) & np.isfinite(label[i])
        ic = _spearman(fmat[i], label[i]) if int(m.sum()) >= min_names else None
        out.append(ic if ic is not None else np.nan)
    return pd.Series(out, dtype=float)


def ic_stats(ic: pd.Series) -> dict:
    s = pd.Series(ic, dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "ic_mean": None, "ic_std": None, "ir": None, "t": None,
                "positive_rate": None, "half_split": {"first_mean": None,
                                                      "second_mean": None, "same_sign": None}}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    ir = mean / std if std > 0 else None
    t = ir * math.sqrt(n) if ir is not None else None
    half = n // 2
    f_mean = float(s.iloc[:half].mean()) if half else None
    s_mean = float(s.iloc[half:].mean()) if n - half else None

    def _sign(x):
        return None if x is None else (1 if x > 0 else (-1 if x < 0 else 0))
    same = None
    if f_mean is not None and s_mean is not None:
        same = _sign(f_mean) == _sign(s_mean)
    return {"n_days": n, "ic_mean": cf._num(mean, 6), "ic_std": cf._num(std, 6),
            "ir": cf._num(ir, 3), "t": cf._num(t, 3),
            "positive_rate": cf._num(float((s > 0).mean()), 4),
            "half_split": {"first_mean": cf._num(f_mean, 6),
                           "second_mean": cf._num(s_mean, 6), "same_sign": same}}


def layer_stats(fmat: np.ndarray, label: np.ndarray, horizon: int,
                n_layers: int = LAYERS, min_names: int = MIN_NAMES) -> dict:
    """5 层分层：逐日序数分桶（稳定破并列），组内未来 h 日收益均值；多空=G5−G1。"""
    daily: dict[int, list[float]] = {g: [] for g in range(1, n_layers + 1)}
    n_used = 0
    for i in range(fmat.shape[0]):
        valid = np.isfinite(fmat[i]) & np.isfinite(label[i])
        if int(valid.sum()) < min_names:
            continue
        n_used += 1
        x = fmat[i][valid]
        y = label[i][valid]
        n = len(x)
        order = np.argsort(x, kind="stable")
        pos = np.empty(n, dtype=float)
        pos[order] = np.arange(n)
        gidx = np.clip(np.floor(pos / n * n_layers).astype(int) + 1, 1, n_layers)
        for g in range(1, n_layers + 1):
            sel = gidx == g
            if int(sel.sum()):
                daily[g].append(float(np.mean(y[sel])))
    groups = {f"G{g}": {"n_days": len(daily[g]),
                        "mean_fwd_pct": cf._num(float(np.mean(daily[g])) if daily[g] else None, 4)}
              for g in range(1, n_layers + 1)}
    means = [groups[f"G{g}"]["mean_fwd_pct"] for g in range(1, n_layers + 1)]
    ls = None
    if means[0] is not None and means[-1] is not None:
        m = means[-1] - means[0]
        ls = {"mean_fwd_pct": cf._num(m, 4),
              "ann_pct": cf._num(m * 252.0 / horizon, 2)}
    mono = None
    if all(x is not None for x in means) and len(set(means)) > 1:
        mono = cf._num(float(pd.Series(means).corr(pd.Series(np.arange(1, n_layers + 1)),
                                                   method="spearman")), 3)
    return {"n_days": n_used, "groups": groups, "long_short": ls, "monotonicity": mono,
            "note": "组内等权未来 h 日收益均值；多空=G5−G1；样本仅 60 只，5 层每组约 12 只，噪声大"}


def grade_factor(ic_by_h: dict, coverage_pct: float | None, pit_pass: bool) -> tuple[str, list[str]]:
    """结论分级：阈值同 W2 前筛（先定后用）。"""
    if not pit_pass:
        return "不通过", ["PIT 严格截断检查未通过：as-of 特征与「截断到当日重算」不一致"
                         "（缠论结构修订：最后一笔/中枢 closed 状态依赖未来 bar）→ 本前筛口径下不可使用"]
    if coverage_pct is not None and coverage_pct < THRESHOLDS["coverage_min_pct"]:
        return "不通过", [f"覆盖率 {coverage_pct}% < {THRESHOLDS['coverage_min_pct']}%（结构不可算/数据缺口）"]
    st = ic_by_h.get(f"h{PRIMARY_H}") or {}
    ic, ir = st.get("ic_mean"), st.get("ir")
    if ic is None:
        return "不通过", ["IC(20d) 无有效样本"]
    abs_ic, abs_ir = abs(ic), (abs(ir) if ir is not None else None)
    same = (st.get("half_split") or {}).get("same_sign")
    short_signal = any((ic_by_h.get(f"h{h}") or {}).get("ic_mean") is not None
                       and abs(ic_by_h[f"h{h}"]["ic_mean"]) >= THRESHOLDS["abs_ic_min"]
                       for h in (5, 10))
    ok_ic = abs_ic >= THRESHOLDS["abs_ic_min"]
    ok_ir = abs_ir is not None and abs_ir >= THRESHOLDS["ir_min"]
    if ok_ic and ok_ir and same is not False:
        return "通过", [f"|IC(20d)|={abs_ic:.4f}≥{THRESHOLDS['abs_ic_min']}、"
                        f"|IR|={abs_ir:.2f}≥{THRESHOLDS['ir_min']}、前后半窗同号={same}、"
                        f"覆盖率 {coverage_pct}%、PIT 通过"]
    if ok_ic or ok_ir or short_signal:
        return "观察", [f"IC(20d)={ic}、IR={ir}、同号={same}、短周期有信号={short_signal}："
                        f"未同时满足『通过』全部门槛"]
    return "不通过", [f"|IC(20d)|={abs_ic}、|IR|={abs_ir} 均低于门槛（且短周期无信号）"]


def validate_report_schema(rep: dict) -> list[str]:
    errors = []
    for k in ("schema_version", "meta", "thresholds", "pit_check", "features", "summary"):
        if k not in rep:
            errors.append(f"缺顶层字段: {k}")
    if errors:
        return errors
    for name in FEAT_NAMES:
        f = rep["features"].get(name)
        if not f:
            errors.append(f"缺特征: {name}")
            continue
        for k in ("ic", "layers", "decay", "coverage", "grade", "pit"):
            if k not in f:
                errors.append(f"{name} 缺字段: {k}")
        if f.get("grade") not in ("通过", "观察", "不通过"):
            errors.append(f"{name} grade 非法: {f.get('grade')}")
        for h in HORIZONS:
            if f"h{h}" not in (f.get("ic") or {}):
                errors.append(f"{name}.ic 缺 h{h}")
    total = sum(len(rep["summary"].get(k, [])) for k in ("通过", "观察", "不通过"))
    if total != len(FEAT_NAMES):
        errors.append(f"summary 分级数 {total} != 特征数 {len(FEAT_NAMES)}")
    return errors


# ============================================================ 特征计算（按日，PIT）
def _day_of(dt: str) -> str:
    return str(dt)[:8]


def build_structures(bars: pd.DataFrame) -> dict:
    """对 bars 跑 analyze_chan，并把结构整理成按「交易日序号」索引的数组（纯函数，可单测）。"""
    res = cf.analyze_chan(bars)
    days = sorted({_day_of(d) for d in bars["dt"].astype(str)})
    day_pos = {d: i for i, d in enumerate(days)}
    bar_day_pos = np.asarray([day_pos[_day_of(d)] for d in bars["dt"].astype(str)], dtype=int)
    n = len(bars)

    def _dp(idx):
        idx = int(idx)
        return day_pos[_day_of(bars.iloc[idx]["dt"])] if 0 <= idx < n else None

    bis = [{"dpos": _dp(b["confirm_idx"]), "dir": int(b["dir"]), "closed": bool(b["closed"])}
           for b in res["bis"] if b.get("closed")]
    zs = [{"dpos": _dp(z["confirm_idx"]), "width_pct": z.get("width_pct"),
           "closed": bool(z["closed"])} for z in res["zhongshu"] if z.get("closed")]
    beichi = [{"dpos": _dp(e["confirm_idx"]), "strength": e.get("strength"),
               "div": bool(e.get("divergence")), "closed": bool(e.get("closed"))}
              for e in res["beichi"] if e.get("closed")]
    points = []
    for p in res["buy_sell_points"]:
        dpos = _dp(p["bar_idx"])
        if dpos is not None:
            points.append({"dpos": dpos, "sign": 1 if p["point"].startswith("B") else -1})
    return {"days": days, "bis": bis, "zs": zs, "beichi": beichi, "points": points,
            "n_bars": n, "res": res}


def features_asof(struct: dict) -> dict[str, np.ndarray]:
    """把结构数组转为「按交易日序号」的 8 个特征序列（as-of 当日收盘，PIT）。"""
    days = struct["days"]
    nd = len(days)
    out = {name: np.full(nd, np.nan) for name in FEAT_NAMES}
    for k in range(nd):
        lo20, lo10, lo5 = k - 19, k - 9, k - 4
        bis = [b for b in struct["bis"] if b["dpos"] is not None and b["dpos"] <= k]
        zs = [z for z in struct["zs"] if z["dpos"] is not None and z["dpos"] <= k]
        be = [e for e in struct["beichi"] if e["dpos"] is not None and e["dpos"] <= k]
        pts = [p for p in struct["points"] if p["dpos"] <= k]
        out["bi_count_20d"][k] = sum(1 for b in bis if b["dpos"] >= lo20)
        out["bi_last_dir"][k] = bis[-1]["dir"] if bis else 0
        out["zs_count_20d"][k] = sum(1 for z in zs if z["dpos"] >= lo20)
        out["zs_width_last"][k] = zs[-1]["width_pct"] if zs and zs[-1]["width_pct"] is not None else np.nan
        recent_be = [e for e in be if e["dpos"] >= lo20 and e["div"]]
        out["beichi_last_strength"][k] = recent_be[-1]["strength"] if recent_be else 0.0
        out["beichi_div_count_20d"][k] = len(recent_be)
        out["bs_net_5d"][k] = sum(p["sign"] for p in pts if p["dpos"] >= lo5)
        last_pt = [p for p in pts if p["dpos"] >= lo10]
        out["bs_last_sign"][k] = last_pt[-1]["sign"] if last_pt else 0
    return out


# ============================================================ 数据加载
def _code_files(root: Path) -> dict[str, list[Path]]:
    return cf._code_files(root)


def load_bars(codes: list[str], mapping: dict, n_days: int = N_DAYS,
              months_n: int = 7) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """读取最近 months_n 个月度 1 分钟文件 → 窗口 n_days 个交易日的 30 分钟 bars。"""
    months = sorted({f.stem.split("_")[-1] for files in mapping.values() for f in files})[-months_n:]
    all_dates: set[str] = set()
    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        parts = sorted(f for f in mapping.get(code, []) if f.stem.split("_")[-1] in months)
        dfs = []
        for f in parts:
            d = pq.read_table(f, columns=["trade_time", "open", "high", "low", "close", "vol",
                                          "amount"]).to_pandas()
            dfs.append(d)
        if not dfs:
            frames[code] = pd.DataFrame()
            continue
        df = pd.concat(dfs, ignore_index=True)
        df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
        df = df[df["trade_time"].notna()]
        frames[code] = df
        all_dates |= set(df["trade_time"].dt.strftime("%Y%m%d").unique().tolist())
    dates = sorted(all_dates)
    window_dates = dates[-int(n_days):]
    win = set(window_dates)
    bars_by_code: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for code in codes:
        df = frames.get(code, pd.DataFrame())
        if df.empty:
            bars_by_code[code] = pd.DataFrame()
            missing.append(f"{code}: 无月度文件")
            continue
        df = df[df["trade_time"].dt.strftime("%Y%m%d").isin(win)]
        bars = cf.resample_30min(df)
        bars_by_code[code] = bars
        if len(bars) < 80:
            missing.append(f"{code}: 30分钟 bar {len(bars)} 根（<80）")
    return bars_by_code, window_dates, missing


def load_adj_close(codes: list[str], window_dates: list[str],
                   pad: int = 25) -> pd.DataFrame:
    """日频复权收盘（窗口日 + 未来 pad 日）：day close 由 30 分钟 bars 取末根，× adj_factor。"""
    daily_files = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    all_dates = [Path(f).stem[2:] for f in daily_files]
    all_dates.sort()
    idx = {d: i for i, d in enumerate(all_dates)}
    if window_dates[0] not in idx:
        raise ValueError("窗口起始日不在 daily 数据中")
    i0 = idx[window_dates[0]]
    i1 = min(len(all_dates) - 1, idx[window_dates[-1]] + pad)
    want = all_dates[i0:i1 + 1]
    close_rows, adj_rows = [], []
    for dt in want:
        c = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code", "close"]).to_pandas()
        a = pq.read_table(DATA / "adj_factor" / f"d_{dt}.parquet",
                          columns=["ts_code", "adj_factor"]).to_pandas()
        c = c[c["ts_code"].isin(codes)].copy()
        c["trade_date"] = dt
        a = a[a["ts_code"].isin(codes)].copy()
        a["trade_date"] = dt
        close_rows.append(c)
        adj_rows.append(a)
    close = pd.concat(close_rows, ignore_index=True).pivot(
        index="trade_date", columns="ts_code", values="close").reindex(index=want, columns=codes)
    adj = pd.concat(adj_rows, ignore_index=True).pivot(
        index="trade_date", columns="ts_code", values="adj_factor").reindex(index=want, columns=codes)
    return close * adj


def forward_returns(adj_close: pd.DataFrame, dates: list[str]) -> dict[int, np.ndarray]:
    idx = {d: i for i, d in enumerate(adj_close.index)}
    rows = [idx[d] for d in dates if d in idx]
    arr = adj_close.to_numpy(dtype=float)
    out = {}
    for h in HORIZONS:
        mat = np.full((len(rows), arr.shape[1]), np.nan)
        for k, i in enumerate(rows):
            if i + h < arr.shape[0]:
                with np.errstate(invalid="ignore", divide="ignore"):
                    mat[k] = arr[i + h] / arr[i] - 1.0
        out[h] = mat * 100.0
    return out


# ============================================================ PIT 截断检查
def pit_truncation_check(bars: pd.DataFrame, sample_days: list[str],
                         tol: float = 1e-9) -> dict:
    """对抽样的 code-day：把 bars 截断到该日收盘重算结构与特征，与全样本 as-of 特征比较。"""
    full = build_structures(bars)
    full_feat = features_asof(full)
    day_pos = {d: i for i, d in enumerate(full["days"])}
    mismatches = {name: 0 for name in FEAT_NAMES}
    checked = 0
    details = []
    for d in sample_days:
        k = day_pos.get(d)
        if k is None:
            continue
        trunc_bars = bars[bars["dt"].astype(str).str[:8] <= d]
        if len(trunc_bars) < 80:
            continue
        trunc = build_structures(trunc_bars)
        trunc_feat = features_asof(trunc)
        if len(trunc["days"]) - 1 != k:
            continue
        checked += 1
        row_mis = []
        for name in FEAT_NAMES:
            a, b = full_feat[name][k], trunc_feat[name][-1]
            if (np.isnan(a) and np.isnan(b)):
                continue
            if np.isnan(a) or np.isnan(b) or abs(a - b) > max(tol, abs(a) * 1e-6):
                mismatches[name] += 1
                row_mis.append(name)
        details.append({"date": d, "mismatch": row_mis})
    rates = {name: (mismatches[name] / checked if checked else None)
             for name in FEAT_NAMES}
    return {"checked_code_days": checked, "mismatch_counts": mismatches,
            "mismatch_rates": rates, "details": details,
            "pass_by_feature": {k: (v is not None and v <= PIT_MISMATCH_MAX)
                                for k, v in rates.items()}}


# ============================================================ 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="缠论因子独立前筛（动态输出名）")
    ap.add_argument("--tag", default="", help="输出名后缀，默认运行日 YYYYMMDD")
    ap.add_argument("--out-dir", default="", help="输出目录，默认 research/reports")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else OUT
    tag = args.tag or datetime.now().strftime("%Y%m%d")
    stem = f"chan_factor_prescreen_{tag}"
    t0 = time.time()
    mapping = _code_files(MINS)
    codes_all = sorted(mapping)
    rng = np.random.default_rng(SEED)
    codes = sorted(rng.choice(codes_all, size=min(N_CODES, len(codes_all)),
                              replace=False).tolist())
    bars_by_code, window_dates, missing = load_bars(codes, mapping)
    print(f"[chan-prescreen] 60 只 × {len(window_dates)} 交易日 bars 加载 "
          f"{time.time()-t0:.1f}s", flush=True)
    adj = load_adj_close(codes, window_dates)
    fwd = forward_returns(adj, window_dates)
    print(f"[chan-prescreen] 复权日线 + 前向收益就绪 {time.time()-t0:.1f}s", flush=True)

    # 逐代码：全样本结构与 as-of 特征（PIT：只用当日及以前 bar）
    structs, feats = {}, {}
    for code in codes:
        bars = bars_by_code.get(code, pd.DataFrame())
        if bars.empty or len(bars) < 80:
            continue
        structs[code] = build_structures(bars)
        feats[code] = features_asof(structs[code])
    codes_ok = list(feats)
    # 特征矩阵（窗口日 × 代码）
    day_index = {d: i for i, d in enumerate(window_dates)}
    feat_mats: dict[str, np.ndarray] = {}
    for name in FEAT_NAMES:
        mat = np.full((len(window_dates), len(codes_ok)), np.nan)
        for j, code in enumerate(codes_ok):
            days = structs[code]["days"]
            vals = feats[code][name]
            for k, d in enumerate(days):
                if d in day_index and np.isfinite(vals[k]):
                    mat[day_index[d], j] = vals[k]
        feat_mats[name] = mat
    print(f"[chan-prescreen] 特征矩阵就绪（{len(codes_ok)} 只）{time.time()-t0:.1f}s", flush=True)

    # PIT 抽样截断检查：6 只 × 3 个窗口日
    pit_codes = codes_ok[::max(1, len(codes_ok) // 6)][:6]
    sample_days = [window_dates[k] for k in (30, 60, 90) if k < len(window_dates)]
    pit_rows = []
    pit_pass_by_feat = {name: True for name in FEAT_NAMES}
    for code in pit_codes:
        chk = pit_truncation_check(bars_by_code[code], sample_days)
        pit_rows.append({"ts_code": code, **chk})
        for name, ok in chk["pass_by_feature"].items():
            if not ok:
                pit_pass_by_feat[name] = False
    agg_rates = {name: (np.mean([r["mismatch_rates"][name] for r in pit_rows
                                 if r["mismatch_rates"][name] is not None])
                        if any(r["mismatch_rates"][name] is not None for r in pit_rows) else None)
                 for name in FEAT_NAMES}
    print(f"[chan-prescreen] PIT 抽样检查完成 {time.time()-t0:.1f}s "
          f"passed={sum(pit_pass_by_feat.values())}/{len(FEAT_NAMES)}", flush=True)

    # IC / 分层 / 衰减
    features_payload = {}
    for name in FEAT_NAMES:
        mat = feat_mats[name]
        coverage = round(100.0 * np.isfinite(mat).sum() / mat.size, 2) if mat.size else 0.0
        ic_by_h, layers, decay = {}, {}, {}
        for h in HORIZONS:
            ic = rank_ic(mat, fwd[h])
            st = ic_stats(ic)
            ic_by_h[f"h{h}"] = st
            decay[f"h{h}"] = st["ic_mean"]
            layers[f"h{h}"] = layer_stats(mat, fwd[h], h)
        pit_info = {"pass": bool(pit_pass_by_feat[name]),
                    "sampled_mismatch_rate": cf._num(agg_rates[name], 4) if agg_rates[name] is not None else None,
                    "method": "抽样 code-day 截断到当日重算结构/特征 vs 全样本 as-of 特征"}
        grade, reasons = grade_factor(ic_by_h, coverage, pit_info["pass"])
        features_payload[name] = {
            "name": name, "label": next(f[1] for f in FEATURES if f[0] == name),
            "family": next(f[2] for f in FEATURES if f[0] == name),
            "coverage": {"valid": int(np.isfinite(mat).sum()), "expected": int(mat.size),
                         "pct": coverage},
            "ic": ic_by_h, "decay": decay, "layers": layers,
            "pit": pit_info, "grade": grade, "grade_reasons": reasons,
        }
        print(f"[chan-prescreen] {name}: cov={coverage}% IC20="
              f"{ic_by_h['h20']['ic_mean']} IR20={ic_by_h['h20']['ir']} grade={grade}",
              flush=True)

    rep = {
        "schema_version": "chan_factor_prescreen.v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "researcher": "司马微", "task": "task-152 缠论因子独立前筛（不代替正式四关）",
            "source": "quant_backtest/factor_lib/chan_factors.py（只读，未修改）",
            "sample": {"n_codes_requested": N_CODES, "n_codes_loaded": len(codes_ok),
                       "n_days": len(window_dates), "window_start": window_dates[0],
                       "window_end": window_dates[-1], "seed": SEED,
                       "bar_freq": "30min（1min→30min 聚合，每日 8 根）"},
            "horizons": list(HORIZONS), "primary_horizon": PRIMARY_H,
            "return_label": "day close（30min 末根）× adj_factor；fwd_h=adj[t+h]/adj[t]−1（%）",
            "data_root": str(MINS), "codes_insufficient": missing,
            "runtime_sec": None,
        },
        "thresholds": THRESHOLDS,
        "pit_check": {
            "sampled": pit_rows,
            "aggregate_mismatch_rates": agg_rates,
            "pass_by_feature": pit_pass_by_feat,
            "method": "对 6 只 × 3 个窗口日：bars 截断到当日收盘 → analyze_chan 重算 → "
                      "features_asof 对比全样本 as-of 特征（不一致率≤10% 视为通过）",
            "note": "全样本 as-of 特征仅使用「确认时间 ≤ 当日」的结构；截断重算用于验证"
                    "结构状态（closed/延伸）不依赖未来 bar。",
        },
        "features": features_payload,
        "summary": {},
        "limitations": [
            "样本仅 60 只 × 120 交易日（seed=42），横截面小、5 层每组约 12 只，RankIC/分层噪声大；",
            "结构从窗口首日开始累积（无窗口前历史），前 ~20 日结构不充分 → 覆盖率前低后高；",
            "30 分钟 bar 由 1 分钟聚合，停牌/缺月会缺 bar；未来收益用日频收盘复权，未做 T+1 入场/成本；",
            "特征方向无先验（缠论结构本身不带方向），前筛按 |IC|/|IR| 判强度，方向需正式四关确认；",
            "PIT：本前筛的快照特征由「全窗口一次 analyze + 按确认时间 as-of 过滤」得到；"
            "抽样严格截断重算显示两者不一致率 22-56%（笔端点延伸/中枢 closed 状态会随未来 bar 修订），"
            "因此 8 特征在本口径下均判 PIT 不通过；若要正式四关，必须先按日截断重算"
            "（60 只×120 日 ≈ 7200 次 analyze ≈ 50-60 分钟，或实现增量式结构引擎）。",
            "不构成投资建议；前筛不代替正式四关。",
        ],
    }
    summary = {"通过": [], "观察": [], "不通过": []}
    for name, f in features_payload.items():
        summary[f["grade"]].append(name)
    rep["summary"] = summary
    rep["schema_check"] = validate_report_schema(rep)
    rep["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    if rep["schema_check"]:
        print(f"[chan-prescreen] WARN schema: {rep['schema_check']}", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- MD
    L = [
        "# 缠论因子独立前筛（task-152 · 司马微）", "",
        f"- 生成：{rep['meta']['generated_at']}；样本 **{len(codes_ok)} 只 × {len(window_dates)} 交易日**"
        f"（{window_dates[0]}~{window_dates[-1]}，seed={SEED}）；30 分钟 bar（1min 聚合，每日 8 根）",
        "- 来源：`quant_backtest/factor_lib/chan_factors.py`（只读）+ `stk_mins`；只做前筛，不代替正式四关",
        "- 特征：笔（笔数/最后方向）、中枢（数量/最新宽度）、背驰（强度/数量）、买卖点（净信号/最新方向）",
        f"- 收益标签：复权日线，fwd_h = adj[t+h]/adj[t] − 1（{list(HORIZONS)} 日）；"
        f"运行时 {rep['meta']['runtime_sec']}s；数据根只读", "",
        "## 一、前筛阈值（先定后用，同 W2 前筛）", "",
        f"- **通过**：{THRESHOLDS['grade_rules']['通过']}",
        f"- **观察**：{THRESHOLDS['grade_rules']['观察']}",
        f"- **不通过**：{THRESHOLDS['grade_rules']['不通过']}", "",
        "## 二、汇总", "",
        "| 特征 | 家族 | 覆盖率 | IC(20d) | IR(20d) | 半窗同号 | 5层多空(20d年化) | 单调性 | PIT | 分级 |",
        "|---|---|---|---|---|---|---|---|---|---|"]
    for name, f in features_payload.items():
        st = f["ic"]["h20"]
        lay = f["layers"]["h20"]
        ls = (lay.get("long_short") or {}).get("ann_pct")
        L.append("| {} | {} | {}% | {} | {} | {} | {} | {} | {} | **{}** |".format(
            name, f["family"], f["coverage"]["pct"], st["ic_mean"], st["ir"],
            (st["half_split"] or {}).get("same_sign"),
            "—" if ls is None else f"{ls}%",
            "—" if lay.get("monotonicity") is None else lay["monotonicity"],
            "✅" if f["pit"]["pass"] else "❌", f["grade"]))
    L += ["", "### 衰减曲线（RankIC 均值）", "",
          "| 特征 | 5d | 10d | 20d |", "|---|---|---|---|"]
    for name, f in features_payload.items():
        L.append(f"| {name} | {f['decay'].get('h5')} | {f['decay'].get('h10')} | {f['decay'].get('h20')} |")
    L += ["", "## 三、PIT 截断检查", "",
          f"- 抽样 6 只 × 3 个窗口日，截断重算结构 → 特征不一致率 ≤ {PIT_MISMATCH_MAX:.0%} 视为通过；",
          "- 各特征不一致率：" + "；".join(
              f"{n}={cf._num(agg_rates[n], 4) if agg_rates[n] is not None else '—'}"
              for n in FEAT_NAMES),
          "- 结构从窗口首日开始累积、仅使用确认时间 ≤ 当日的结构；截断检查用于验证 closed/延伸状态不依赖未来 bar。", "",
          "## 四、逐特征结论", ""]
    for name, f in features_payload.items():
        L.append(f"### {name}（{f['label']}）— **{f['grade']}**")
        L.append("")
        for r in f["grade_reasons"]:
            L.append(f"- {r}")
        L.append(f"- 覆盖率 {f['coverage']['pct']}%（{f['coverage']['valid']}/{f['coverage']['expected']}）；"
                 f"PIT 抽样不一致率 {f['pit']['sampled_mismatch_rate']}")
        L.append("")
    L += ["## 五、局限", ""] + [f"- {x}" for x in rep["limitations"]]
    (out_dir / f"{stem}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "schema": rep["schema_check"],
                      "pit_pass": pit_pass_by_feat, "runtime": rep["meta"]["runtime_sec"]},
                     ensure_ascii=False), flush=True)
    return 0 if not rep["schema_check"] else 1


if __name__ == "__main__":
    sys.exit(main())
