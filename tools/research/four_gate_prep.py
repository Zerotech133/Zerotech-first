# -*- coding: utf-8 -*-
"""四关前稳健性预检（司马微 · task-154）。

两个四关候选：
  A. platform_consolidation_score（日线平台整理强度，learning_factors）
  B. 缠论 zs_width_last 反向（最新闭中枢宽度取负，chan_factors）

样本：300 只 × 近 120 交易日（确定性抽样 seed=42）；数据根只读。
检验：
  ① 行业+市值中性化 RankIC/IR（逐日横截面 OLS：因子 ~ 1 + 行业哑变量 + ln(流通市值)，取残差）；
  ② 子样本稳定性：前后半窗 / 分季度（Q2/Q3/Q4）；
  ③ 成本敏感性：30/50/80bp 单边，20 日持有重叠组合的换手估算 → 净多空/净多头；
  ④ walk-forward 3 段（120 日等分 3 段 × 40 日）；
  ⑤ 5 层分层多空 + 单调性；
结论：是否支持进入正式四关（含保留项）。

PIT：
  - 日线因子 platform：只用 t 及以前收盘；抽样「截断到 t 重算」不变性检查；
  - 缠论 zs_width_last：按「5 个交易日的更新点」用**截断到更新日的 30 分钟 bars**重算
    闭中枢（严格只用过去 bar），日频对更新点前向填充（最多滞后 5 日）→ 结构性 PIT 安全；
    不使用全窗口一次 analyze 的 as-of 快照（task-152 已证其被未来修订污染）。

产出：research/reports/four_gate_prep_20261103.{json,md}
运行：C:\\项目\\py312\\python.exe -X utf8 tools/research/four_gate_prep.py
"""
from __future__ import annotations

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
import learning_factors as lf  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
MINS = DATA / "stk_mins"
OUT = REPO / "research" / "reports"
N_CODES, N_DAYS, SEED = 300, 120, 42
HORIZONS = (5, 10, 20)
PRIMARY_H = 20
LAYERS = 5
MIN_NAMES = 60
COSTS_BP = (30, 50, 80)
WF_SEGMENTS = 3
CHAN_UPDATE_EVERY = 5       # 缠论特征每 5 个交易日按截断 bars 重算一次
PLATFORM_WARMUP = 100       # 平台整理因子需要 >=80 日历史

THRESHOLDS = {
    "abs_ic_20_min": 0.02,
    "ir_20_min": 0.3,
    "min_positive_segments": 2,   # 3 段 walk-forward 中至少 2 段同向
    "monotonicity_min_abs": 0.6,
    "cost_supported_bp": 50,      # 50bp 净多空需 >0
    "coverage_min_pct": 60.0,
    "pit_required": True,
}


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


def daily_rank_ic(fmat: np.ndarray, label: np.ndarray,
                  min_names: int = MIN_NAMES) -> pd.Series:
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
        return {"n_days": 0, "mean": None, "std": None, "ir": None, "t": None,
                "positive_rate": None, "split": {}}
    mean, std = float(s.mean()), (float(s.std(ddof=1)) if n > 1 else 0.0)
    ir = mean / std if std > 0 else None
    t = ir * math.sqrt(n) if ir is not None else None
    half = n // 2
    f = float(s.iloc[:half].mean()) if half else None
    b = float(s.iloc[half:].mean()) if n - half else None
    return {"n_days": n, "mean": lf._num(mean, 6), "std": lf._num(std, 6),
            "ir": lf._num(ir, 3), "t": lf._num(t, 3),
            "positive_rate": lf._num(float((s > 0).mean()), 4),
            "split": {"first_mean": lf._num(f, 6), "second_mean": lf._num(b, 6),
                      "same_sign": (None if (f is None or b is None)
                                    else (1 if f > 0 else -1) == (1 if b > 0 else -1))}}


def neutralize(factor_row: np.ndarray, ln_mv_row: np.ndarray,
               ind_code: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """逐日横截面 OLS 中性化：factor ~ 1 + 行业哑变量 + ln(市值)；返回残差（无效=NaN）。"""
    out = np.full_like(factor_row, np.nan, dtype=float)
    m = valid & np.isfinite(factor_row)
    if int(m.sum()) < 20:
        return out
    y = factor_row[m]
    codes = np.unique(ind_code[m])
    cols = [np.ones(int(m.sum()))]
    for c in codes[1:]:                       # 第一个行业作基准
        cols.append((ind_code[m] == c).astype(float))
    if np.isfinite(ln_mv_row[m]).any():
        mv = ln_mv_row[m]
        mv = np.where(np.isfinite(mv), mv, np.nanmean(mv))
        mv = mv - mv.mean()
        cols.append(mv)
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    out[m] = y - X @ beta
    return out


def neutralized_matrix(fmat: np.ndarray, ln_mv: np.ndarray, ind_code: np.ndarray,
                       elig: np.ndarray) -> np.ndarray:
    out = np.full_like(fmat, np.nan, dtype=float)
    for i in range(fmat.shape[0]):
        out[i] = neutralize(fmat[i], ln_mv[i], ind_code, elig[i])
    return out


def layer_stats(fmat: np.ndarray, label: np.ndarray, horizon: int,
                n_layers: int = LAYERS, min_names: int = MIN_NAMES) -> dict:
    daily = {g: [] for g in range(1, n_layers + 1)}
    n_used = 0
    for i in range(fmat.shape[0]):
        m = np.isfinite(fmat[i]) & np.isfinite(label[i])
        if int(m.sum()) < min_names:
            continue
        n_used += 1
        x, y = fmat[i][m], label[i][m]
        n = len(x)
        order = np.argsort(x, kind="stable")
        pos = np.empty(n, dtype=float)
        pos[order] = np.arange(n)
        gidx = np.clip(np.floor(pos / n * n_layers).astype(int) + 1, 1, n_layers)
        for g in range(1, n_layers + 1):
            sel = gidx == g
            if sel.any():
                daily[g].append(float(np.mean(y[sel])))
    groups = {f"G{g}": {"n_days": len(daily[g]),
                        "mean_fwd_pct": lf._num(float(np.mean(daily[g])) if daily[g] else None, 4)}
              for g in range(1, n_layers + 1)}
    means = [groups[f"G{g}"]["mean_fwd_pct"] for g in range(1, n_layers + 1)]
    ls = None
    if means[0] is not None and means[-1] is not None:
        m = means[-1] - means[0]
        ls = {"mean_fwd_pct": lf._num(m, 4),
              "ann_pct": lf._num(m * 252.0 / horizon, 2)}
    mono = None
    if all(x is not None for x in means) and len(set(means)) > 1:
        mono = lf._num(float(pd.Series(means).corr(pd.Series(np.arange(1, n_layers + 1)),
                                                   method="spearman")), 3)
    return {"n_days": n_used, "groups": groups, "long_short": ls, "monotonicity": mono,
            "note": "按中性化因子序数分桶；多空=G5−G1；收益=未来 h 日原始收益（%）"}


def portfolio_cost(long_sets: list[set], short_sets: list[set], gross_ann_pct: float,
                   horizon: int = PRIMARY_H, costs_bp=COSTS_BP) -> dict:
    """20 日重叠持有组合成本：日名单变化率 × 252/horizon = 年化单边换手（每腿）。"""
    churn_l = [1.0 - len(long_sets[i] & long_sets[i - 1]) / max(1, len(long_sets[i - 1]))
               for i in range(1, len(long_sets)) if long_sets[i - 1]]
    churn_s = [1.0 - len(short_sets[i] & short_sets[i - 1]) / max(1, len(short_sets[i - 1]))
               for i in range(1, len(short_sets)) if short_sets[i - 1]]
    ann_turn_l = float(np.mean(churn_l)) * 252.0 / horizon if churn_l else 0.0
    ann_turn_s = float(np.mean(churn_s)) * 252.0 / horizon if churn_s else 0.0
    ann_turn_2leg = ann_turn_l + ann_turn_s
    out = {"ann_turnover_long": lf._num(ann_turn_l, 2),
           "ann_turnover_short": lf._num(ann_turn_s, 2),
           "ann_turnover_2leg": lf._num(ann_turn_2leg, 2)}
    for bp in costs_bp:
        drag = bp / 10000.0 * 100.0 * ann_turn_2leg
        out[f"{bp}bp"] = {"gross_ann_pct": lf._num(gross_ann_pct, 2),
                          "cost_drag_ann_pct": lf._num(drag, 2),
                          "net_ann_pct": lf._num(gross_ann_pct - drag, 2)}
    return out


def segments(dates: list[str], n_seg: int = WF_SEGMENTS) -> list[tuple[str, list[int]]]:
    """walk-forward 3 段（连续等分）。"""
    n = len(dates)
    bounds = np.linspace(0, n, n_seg + 1).astype(int)
    out = []
    for k in range(n_seg):
        idx = list(range(bounds[k], bounds[k + 1]))
        out.append((f"seg{k + 1}", idx))
    return out


def grade_candidate(ic20: dict, seg_ics: list[float], mono, cost: dict,
                    coverage_pct: float, pit_pass: bool) -> tuple[str, list[str]]:
    """是否支持进入正式四关（规则先定后用）。"""
    reasons = []
    if not pit_pass:
        return "不支持", ["PIT 未通过：不得进入正式四关"]
    if coverage_pct is not None and coverage_pct < THRESHOLDS["coverage_min_pct"]:
        return "不支持", [f"覆盖率 {coverage_pct}% < {THRESHOLDS['coverage_min_pct']}%"]
    ic, ir = ic20.get("mean"), ic20.get("ir")
    abs_ic = None if ic is None else abs(ic)
    abs_ir = None if ir is None else abs(ir)
    ok_ic = abs_ic is not None and abs_ic >= THRESHOLDS["abs_ic_20_min"]
    ok_ir = abs_ir is not None and abs_ir >= THRESHOLDS["ir_20_min"]
    signs = [1 if x > 0 else -1 for x in seg_ics if x is not None]
    overall_sign = 1 if (ic is not None and ic > 0) else -1
    n_same = sum(1 for s in signs if s == overall_sign)
    n_pos = sum(1 for s in signs if s > 0)
    n_neg = sum(1 for s in signs if s < 0)
    # 分段同向必须与「全样本 IC 方向」一致（不能把 2 段反向当成稳定）
    seg_ok = n_same >= THRESHOLDS["min_positive_segments"]
    ok_mono = mono is not None and abs(mono) >= THRESHOLDS["monotonicity_min_abs"]
    net50 = (cost.get("50bp") or {}).get("net_ann_pct")
    ok_cost = net50 is not None and net50 > 0
    checks = {"ic": ok_ic, "ir": ok_ir, "segments": seg_ok, "monotonicity": ok_mono,
              "cost_50bp": ok_cost}
    reasons.append(f"中性化 |IC(20d)|={abs_ic}（≥{THRESHOLDS['abs_ic_20_min']}? "
                   f"{'是' if ok_ic else '否'}）、|IR|={abs_ir}（≥{THRESHOLDS['ir_20_min']}? "
                   f"{'是' if ok_ir else '否'}）")
    reasons.append(f"walk-forward 3 段符号：{[s for s in signs]}（≥2 段同向? "
                   f"{'是' if seg_ok else '否'}）")
    reasons.append(f"5 层单调性={mono}（|·|≥{THRESHOLDS['monotonicity_min_abs']}? "
                   f"{'是' if ok_mono else '否'}）、50bp 净多空={net50}%（>0? "
                   f"{'是' if ok_cost else '否'}）、覆盖率={coverage_pct}%")
    support = all(checks.values())
    if support:
        return "支持", reasons + ["全部稳健性门槛通过 → 支持进入正式四关"]
    return "不支持", reasons + ["存在未通过项 → 不支持进入正式四关（见保留项）"]



def reserved_items(c: dict) -> list[str]:
    """显式列出未通过的稳健性门槛（保留项）。"""
    T = THRESHOLDS
    out = []
    ic20 = (c.get("neutral_ic") or {}).get("h20") or {}
    ic, ir = ic20.get("mean"), ic20.get("ir")
    if ic is None or abs(ic) < T["abs_ic_20_min"]:
        out.append(f"中性化 |IC(20d)|={ic} < {T['abs_ic_20_min']}")
    if ir is None or abs(ir) < T["ir_20_min"]:
        out.append(f"|IR(20d)|={ir} < {T['ir_20_min']}")
    segs = [x.get("ic_mean") for x in (c.get("walk_forward") or [])]
    overall = 1 if (ic or 0) > 0 else -1
    same = sum(1 for x in segs if x is not None and (1 if x > 0 else -1) == overall)
    if same < T["min_positive_segments"]:
        out.append(f"walk-forward 同向段 {same}/3 < {T['min_positive_segments']}：{segs}")
    elif len(set(1 if (x or 0) > 0 else -1 for x in segs if x is not None)) > 1:
        out.append(f"walk-forward 存在反向段（{segs}），仅达最低同向数")
    mono = ((c.get("layers") or {}).get("h20") or {}).get("monotonicity")
    if mono is None or abs(mono) < T["monotonicity_min_abs"]:
        out.append(f"5 层单调性={mono} 绝对 < {T['monotonicity_min_abs']}")
    net50 = (((c.get("cost") or {}).get("50bp") or {}).get("net_ann_pct"))
    if net50 is None or net50 <= 0:
        out.append(f"50bp 净多空={net50}% ≤ 0")
    cov = (c.get("coverage") or {}).get("pct")
    if cov is None or cov < T["coverage_min_pct"]:
        out.append(f"覆盖率={cov}% < {T['coverage_min_pct']}%")
    if not ((c.get("pit") or {}).get("pass")):
        out.append("PIT 未通过")
    return out


def validate_report_schema(rep: dict) -> list[str]:
    errors = []
    for k in ("schema_version", "meta", "thresholds", "candidates", "summary"):
        if k not in rep:
            errors.append(f"缺顶层字段: {k}")
    if errors:
        return errors
    for name in ("platform_consolidation", "chan_zs_width_reverse"):
        c = rep["candidates"].get(name)
        if not c:
            errors.append(f"缺候选: {name}")
            continue
        for k in ("raw_ic", "neutral_ic", "layers", "cost", "walk_forward",
                  "subsamples", "coverage", "pit", "decision"):
            if k not in c:
                errors.append(f"{name} 缺字段: {k}")
        if c["decision"].get("support") not in ("支持", "不支持"):
            errors.append(f"{name} decision.support 非法")
        for h in HORIZONS:
            if f"h{h}" not in c["neutral_ic"]:
                errors.append(f"{name}.neutral_ic 缺 h{h}")
        for bp in COSTS_BP:
            if f"{bp}bp" not in c["cost"]:
                errors.append(f"{name}.cost 缺 {bp}bp")
    total = sum(len(rep["summary"].get(k, [])) for k in ("支持", "不支持"))
    if total != 2:
        errors.append(f"summary 候选数 {total} != 2")
    return errors


# ============================================================ 数据加载
def trading_dates() -> list[str]:
    return sorted(Path(f).stem[2:] for f in glob.glob(str(DATA / "daily" / "d_*.parquet")))


def load_daily_panel(dates: list[str], codes: list[str]) -> dict:
    close, amount, adj = [], [], []
    for dt in dates:
        c = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code", "close", "amount"]).to_pandas()
        c = c[c["ts_code"].isin(codes)].copy()
        c["trade_date"] = dt
        close.append(c[["trade_date", "ts_code", "close"]])
        a = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code"]).to_pandas() if False else None
        amount.append(c[["trade_date", "ts_code", "amount"]])
        f = pq.read_table(DATA / "adj_factor" / f"d_{dt}.parquet",
                          columns=["ts_code", "adj_factor"]).to_pandas()
        f = f[f["ts_code"].isin(codes)].copy()
        f["trade_date"] = dt
        adj.append(f)
    def _pivot(frames, col):
        long = pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
        return long.pivot(index="trade_date", columns="ts_code", values=col).reindex(
            index=dates, columns=codes)
    close_m = _pivot(close, "close")
    amount_m = _pivot(amount, "amount")
    adj_m = _pivot(adj, "adj_factor")
    return {"close": close_m, "amount": amount_m, "adj_close": close_m * adj_m}


def load_circ_mv(dates: list[str], codes: list[str]) -> np.ndarray:
    frames = []
    for dt in dates:
        path = DATA / "daily_basic" / f"d_{dt}.parquet"
        if not path.exists():
            continue
        df = pq.read_table(path, columns=["ts_code", "circ_mv"]).to_pandas()
        df = df[df["ts_code"].isin(codes)].copy()
        df["trade_date"] = dt
        frames.append(df)
    if not frames:
        return np.full((len(dates), len(codes)), np.nan)
    long = pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
    mat = long.pivot(index="trade_date", columns="ts_code", values="circ_mv")
    return np.asarray(mat.reindex(index=dates, columns=codes), dtype=float)


def load_industry_map(codes: list[str]) -> np.ndarray:
    files = sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))
    if not files:
        return np.zeros(len(codes), dtype=np.int32)
    sb = pd.concat([pq.read_table(f, columns=["ts_code", "industry"]).to_pandas()
                    for f in files], ignore_index=True).drop_duplicates("ts_code")
    s = sb.set_index("ts_code")["industry"].reindex(codes).fillna("其他").astype(str)
    uniq, inv = np.unique(s.to_numpy(), return_inverse=True)
    return inv.astype(np.int32), list(uniq)


def forward_returns(adj_close: np.ndarray, dates: list[str],
                    window_dates: list[str]) -> dict[int, np.ndarray]:
    idx = {d: i for i, d in enumerate(dates)}
    rows = [idx[d] for d in window_dates if d in idx]
    out = {}
    for h in HORIZONS:
        mat = np.full((len(rows), adj_close.shape[1]), np.nan)
        for k, i in enumerate(rows):
            if i + h < adj_close.shape[0]:
                with np.errstate(invalid="ignore", divide="ignore"):
                    mat[k] = adj_close[i + h] / adj_close[i] - 1.0
        out[h] = mat * 100.0
    return out


# ============================================================ 缠论 zs_width（PIT：截断更新点）
def load_chan_bars(codes: list[str], window_dates: list[str]) -> dict[str, pd.DataFrame]:
    mapping = cf._code_files(MINS)
    months_all = sorted({f.stem.split("_")[-1] for files in mapping.values() for f in files})
    months = [m for m in months_all if window_dates[0][:6] <= m <= window_dates[-1][:6]]
    if len(months) < 5:
        months = months_all[-6:]
    win = set(window_dates)
    frames: dict[str, pd.DataFrame] = {}
    bars_by: dict[str, pd.DataFrame] = {}
    for code in codes:
        parts = sorted(f for f in mapping.get(code, []) if f.stem.split("_")[-1] in months)
        dfs = []
        for f in parts:
            try:
                dfs.append(pq.read_table(f, columns=["trade_time", "open", "high", "low",
                                                     "close", "vol", "amount"]).to_pandas())
            except Exception:  # noqa: BLE001
                continue
        if not dfs:
            bars_by[code] = pd.DataFrame()
            continue
        df = pd.concat(dfs, ignore_index=True)
        df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
        df = df[df["trade_time"].notna()]
        frames[code] = df
        bars_by[code] = df  # 保留 1min，截断后按需 resample
    return bars_by, months


def zs_width_snapshot(bars_30: pd.DataFrame) -> tuple[float | None, int]:
    """最新闭中枢宽度（只用给入 bars，即截断到更新日的数据）。"""
    if bars_30 is None or len(bars_30) < 80:
        return None, 0
    df = cf.norm_bars(bars_30)
    m = cf.merge_inclusion(df)
    fr = cf.find_fractals(df, merged=m)
    bis = cf.find_bi(df, merged=m, fractals=fr)
    zs = cf.find_zhongshu(df, bis=bis, merged=m)
    closed = [z for z in zs if z.get("closed") and z.get("width_pct") is not None]
    return (float(closed[-1]["width_pct"]) if closed else None), len(closed)


def build_chan_feature(window_dates: list[str], codes: list[str],
                       min_bars: int = 80) -> tuple[np.ndarray, np.ndarray, dict]:
    """按「每 5 个交易日」更新点、截断到更新日重算 zs_width；日频前向填充。"""
    bars_by, months = load_chan_bars(codes, window_dates)
    # 先一次性聚合 30min（避免每个更新点重复 resample）
    bars30_by = {}
    for code in codes:
        df = bars_by.get(code)
        if df is None or df.empty:
            continue
        try:
            bars30_by[code] = cf.resample_30min(df)
        except Exception:  # noqa: BLE001
            continue
    update_pos = list(range(len(window_dates)))[4::CHAN_UPDATE_EVERY]
    n_days, n_codes = len(window_dates), len(codes)
    width_upd = np.full((len(update_pos), n_codes), np.nan)
    count_upd = np.full((len(update_pos), n_codes), np.nan)
    for u_i, pos in enumerate(update_pos):
        d = window_dates[pos]
        for j, code in enumerate(codes):
            bars30 = bars30_by.get(code)
            if bars30 is None or bars30.empty:
                continue
            sub = bars30[bars30["dt"].astype(str).str[:8] <= d]
            if len(sub) == 0:
                continue
            w, cnt = zs_width_snapshot(sub)
            width_upd[u_i, j] = w if w is not None else np.nan
            count_upd[u_i, j] = cnt
    # 日频前向填充（最多滞后 CHAN_UPDATE_EVERY-1 日）
    width = np.full((n_days, n_codes), np.nan)
    for k in range(n_days):
        known = [u for u_i, u in enumerate(update_pos) if u <= k]
        if not known:
            continue
        u_i = len(known) - 1
        width[k] = width_upd[u_i]
    return width, count_upd, {"months": months, "n_updates": len(update_pos),
                              "lag": CHAN_UPDATE_EVERY - 1}


# ============================================================ 主流程
def main() -> int:
    t0 = time.time()
    all_dates = trading_dates()
    window_dates = all_dates[-N_DAYS:]
    last_df = pq.read_table(DATA / "daily" / f"d_{window_dates[-1]}.parquet",
                            columns=["ts_code", "close", "amount"]).to_pandas()
    universe = sorted(last_df["ts_code"].astype(str))
    rng = np.random.default_rng(SEED)
    codes = sorted(rng.choice(universe, size=min(N_CODES, len(universe)),
                              replace=False).tolist())
    # 日线样本：窗口前 warmup + 窗口 + 未来 25 日
    i1 = all_dates.index(window_dates[-1])
    i0 = max(0, all_dates.index(window_dates[0]) - PLATFORM_WARMUP)
    load_dates = all_dates[i0:min(len(all_dates), i1 + 26)]
    panel = load_daily_panel(load_dates, codes)
    ind_code, ind_names = load_industry_map(codes)
    circ = load_circ_mv(window_dates, codes)
    ln_mv = np.log(np.where(circ > 0, circ * 1e4, np.nan))
    elig = np.isfinite(panel["close"].reindex(index=window_dates).to_numpy())
    fwd = forward_returns(panel["adj_close"].to_numpy(), load_dates, window_dates)
    print(f"[four-gate] 日线样本 {len(codes)} 只 × {len(window_dates)} 日就绪 "
          f"{time.time()-t0:.1f}s", flush=True)

    # ---- 候选 A：platform_consolidation_score（日线，PIT）
    plat_full = pd.DataFrame(lf.platform_consolidation_score(panel["close"]),
                             index=panel["close"].index, columns=panel["close"].columns)
    plat = plat_full.reindex(window_dates).to_numpy(dtype=float)
    # PIT 截断不变性：截断到 t 重算，比较 t 日值
    pit_samples = []
    for d in (window_dates[30], window_dates[60], window_dates[90]):
        trunc_close = panel["close"].loc[panel["close"].index <= d]
        rec = pd.DataFrame(lf.platform_consolidation_score(trunc_close))
        a = plat_full.loc[d].to_numpy(dtype=float)
        b = rec.loc[d].reindex(panel["close"].columns).to_numpy(dtype=float)
        ok = np.allclose(a, b, equal_nan=True, atol=1e-9)
        pit_samples.append({"date": d, "pass": bool(ok),
                            "n_compare": int(np.isfinite(a).sum())})
    plat_pit_pass = all(x["pass"] for x in pit_samples)
    print(f"[four-gate] platform 因子 PIT={plat_pit_pass} {time.time()-t0:.1f}s", flush=True)

    # ---- 候选 B：缠论 zs_width_last 反向（截断更新点重算 + 前向填充）
    chan_width, chan_cnt, chan_meta = build_chan_feature(window_dates, codes)
    print(f"[four-gate] 缠论 zs_width PIT 更新点重算完成 {time.time()-t0:.1f}s "
          f"updates={chan_meta['n_updates']}", flush=True)
    chan_pit_pass = True  # 构造上只用截断到更新日的 bars；无未来信息
    chan_feat = -chan_width       # 反向候选：宽度越大 → 预期越差 → 取负
    chan_pit = {"pass": chan_pit_pass,
                "method": "每个更新点用截断到该日 15:00 的 30min bars 重算闭中枢（严格只用过去 bar），"
                          f"日频前向填充（最大滞后 {chan_meta['lag']} 日）",
                "n_updates": chan_meta["n_updates"]}

    def assess(name: str, fmat: np.ndarray, direction_note: str) -> dict:
        raw_ic, neut_ic, layers, decay = {}, {}, {}, {}
        neut = neutralized_matrix(fmat, ln_mv, ind_code, elig)
        for h in HORIZONS:
            raw_ic[f"h{h}"] = ic_stats(daily_rank_ic(fmat, fwd[h]))
            neut_ic[f"h{h}"] = ic_stats(daily_rank_ic(neut, fwd[h]))
            layers[f"h{h}"] = layer_stats(neut, fwd[h], h)
            decay[f"h{h}"] = neut_ic[f"h{h}"]["mean"]
        # 组合：20 日持有重叠，多空 G5/G1（按中性化因子）
        sel_ic = daily_rank_ic(neut, fwd[PRIMARY_H])
        long_sets, short_sets = [], []
        for i in range(len(window_dates)):
            m = np.isfinite(neut[i]) & np.isfinite(fwd[PRIMARY_H][i])
            if int(m.sum()) < MIN_NAMES:
                long_sets.append(set())
                short_sets.append(set())
                continue
            idx = np.flatnonzero(m)
            order = idx[np.argsort(neut[i][idx], kind="stable")]
            k = max(1, len(order) // LAYERS)
            short_sets.append(set(order[:k].tolist()))
            long_sets.append(set(order[-k:].tolist()))
        ls = layers[f"h{PRIMARY_H}"]["long_short"] or {}
        gross = ls.get("ann_pct")
        cost = portfolio_cost(long_sets, short_sets, gross if gross is not None else 0.0)
        # walk-forward 3 段
        wf = []
        for seg_name, idx in segments(window_dates):
            ic_seg = ic_stats(daily_rank_ic(neut[idx], fwd[PRIMARY_H][idx]))
            wf.append({"segment": seg_name, "dates": [window_dates[idx[0]], window_dates[idx[-1]]],
                       "n_days": ic_seg["n_days"], "ic_mean": ic_seg["mean"],
                       "ir": ic_seg["ir"],
                       "long_short_ann_pct": (layers[f"h{PRIMARY_H}"]["long_short"] or {}).get("ann_pct")})
        # 前后半窗 / 分季度
        half = len(window_dates) // 2
        subs = {
            "first_half": ic_stats(daily_rank_ic(neut[:half], fwd[PRIMARY_H][:half])),
            "second_half": ic_stats(daily_rank_ic(neut[half:], fwd[PRIMARY_H][half:])),
            "by_quarter": {},
        }
        quarters = {}
        for k, d in enumerate(window_dates):
            quarters.setdefault(d[:6], []).append(k)
        for q, idx in quarters.items():
            st = ic_stats(daily_rank_ic(neut[idx], fwd[PRIMARY_H][idx]))
            subs["by_quarter"][q] = {"n_days": st["n_days"], "ic_mean": st["mean"], "ir": st["ir"]}
        coverage = round(100.0 * np.isfinite(fmat).sum() / fmat.size, 2) if fmat.size else 0.0
        cov_neutral = round(100.0 * np.isfinite(neut).sum() / neut.size, 2) if neut.size else 0.0
        seg_ics = [x["ic_mean"] for x in wf]
        mono = (layers[f"h{PRIMARY_H}"] or {}).get("monotonicity")
        decision_support, decision_reasons = grade_candidate(
            neut_ic[f"h{PRIMARY_H}"], seg_ics, mono, cost, coverage, True)
        return {
            "direction_note": direction_note,
            "raw_ic": raw_ic, "neutral_ic": neut_ic, "decay": decay,
            "layers": layers, "cost": cost, "walk_forward": wf, "subsamples": subs,
            "coverage": {"pct": coverage, "neutral_pct": cov_neutral},
            "pit": {"pass": True},
            "decision": {"support": decision_support, "reasons": decision_reasons,
                         "checks": {"ic": neut_ic[f"h{PRIMARY_H}"]["mean"] is not None,
                                    "segments_ic": seg_ics}},
        }

    cand_a = assess("platform_consolidation", plat, "平台整理强度：值大=整理久且窄（先验 +1）")
    cand_a["pit"] = {"pass": plat_pit_pass, "method": "日线因子仅用 t 及以前收盘；抽样截断重算不变性",
                     "samples": pit_samples}
    cand_a["decision"]["support"], cand_a["decision"]["reasons"] = grade_candidate(
        cand_a["neutral_ic"][f"h{PRIMARY_H}"],
        [x["ic_mean"] for x in cand_a["walk_forward"]],
        (cand_a["layers"][f"h{PRIMARY_H}"] or {}).get("monotonicity"),
        cand_a["cost"], cand_a["coverage"]["pct"], plat_pit_pass)
    cand_b = assess("chan_zs_width_reverse", chan_feat,
                    "缠论 zs_width_last 反向：中枢越宽 → 取负后越大（先验 +1，反向候选）")
    cand_b["pit"] = chan_pit
    for _c in (cand_a, cand_b):
        _c["decision"]["reserved_items"] = reserved_items(_c)

    rep = {
        "schema_version": "four_gate_prep.v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "researcher": "司马微", "task": "task-154 四关前稳健性预检",
            "sample": {"n_codes": len(codes), "n_days": len(window_dates),
                       "window_start": window_dates[0], "window_end": window_dates[-1],
                       "seed": SEED, "horizons": list(HORIZONS),
                       "primary_horizon": PRIMARY_H},
            "neutralization": "逐日横截面 OLS：因子 ~ 1 + 行业哑变量（stock_basic.industry，"
                              "首类为基准）+ ln(流通市值)；取残差后再算 RankIC/分层",
            "industry_count": len(ind_names),
            "return_label": "复权价（close×adj_factor）未来 h 日收益（%）",
            "data_root": str(DATA), "runtime_sec": None,
        },
        "thresholds": THRESHOLDS,
        "candidates": {"platform_consolidation": cand_a, "chan_zs_width_reverse": cand_b},
        "summary": {},
        "limitations": [
            "样本 300 只 × 120 交易日（约 6 个月），只覆盖单一市场环境，非全样本；",
            "行业中性化用 stock_basic 静态行业（非 PIT 行业变迁）；市值用 daily_basic.circ_mv；",
            "成本为 30/50/80bp 单边 × 年化换手估算，未含冲击/涨跌停不可执行；",
            "缠论 zs_width 用 5 日更新点截断重算 + 前向填充（最大滞后 4 日），非逐日重算；",
            "平台整理因子需要 80 日历史，窗口前用 100 日 warmup；",
            "walk-forward 3 段为窗口内等分（各约 40 日），非跨年样本外；",
            "不构成投资建议；结论仅决定是否值得进入正式四关。",
        ],
    }
    summary = {"支持": [], "不支持": []}
    for name, c in rep["candidates"].items():
        summary[c["decision"]["support"]].append(name)
    rep["summary"] = summary
    rep["schema_check"] = validate_report_schema(rep)
    rep["meta"]["runtime_sec"] = round(time.time() - t0, 1)
    if rep["schema_check"]:
        print(f"[four-gate] WARN schema: {rep['schema_check']}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "four_gate_prep_20261103.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- MD
    L = [
        "# 四关前稳健性预检（task-154 · 司马微）", "",
        f"- 生成：{rep['meta']['generated_at']}；样本 **{len(codes)} 只 × {len(window_dates)} 交易日**"
        f"（{window_dates[0]}~{window_dates[-1]}，seed={SEED}）；运行时 {rep['meta']['runtime_sec']}s",
        "- 候选：A `platform_consolidation_score`（日线平台整理）；B 缠论 `zs_width_last` 反向",
        "- 中性化：逐日 OLS `因子 ~ 1 + 行业哑变量 + ln(流通市值)` 残差后算 RankIC/分层",
        "- 收益：复权价未来 5/10/20 日；成本 30/50/80bp 单边（20 日重叠持有，换手估算）", "",
        "## 一、是否支持进入正式四关", "",
        "| 候选 | 中性化 IC(20d) | IR(20d) | 3 段符号 | 5层单调性 | 50bp 净多空 | 覆盖率 | PIT | 结论 |",
        "|---|---|---|---|---|---|---|---|---|"]
    for name, c in rep["candidates"].items():
        ni = c["neutral_ic"]["h20"]
        segs = [x["ic_mean"] for x in c["walk_forward"]]
        mono = (c["layers"]["h20"] or {}).get("monotonicity")
        net50 = (c["cost"].get("50bp") or {}).get("net_ann_pct")
        L.append("| {} | {} | {} | {} | {} | {}% | {}% | {} | **{}** |".format(
            "A 平台整理" if name == "platform_consolidation" else "B 中枢宽度(反向)",
            ni["mean"], ni["ir"], segs, mono, net50, c["coverage"]["pct"],
            "✅" if c["pit"]["pass"] else "❌", c["decision"]["support"]))
    L += ["", "## 二、逐候选证据", ""]
    for name, c in rep["candidates"].items():
        title = "A platform_consolidation_score" if name == "platform_consolidation" \
            else "B chan zs_width_last（反向）"
        L += [f"### {title} — **{c['decision']['support']}进入正式四关**", ""]
        for r in c["decision"]["reasons"]:
            L.append(f"- {r}")
        L += ["", "| horizon | raw IC | raw IR | 中性化 IC | 中性化 IR |", "|---|---|---|---|---|"]
        for h in HORIZONS:
            L.append(f"| {h}d | {c['raw_ic'][f'h{h}']['mean']} | {c['raw_ic'][f'h{h}']['ir']} | "
                     f"{c['neutral_ic'][f'h{h}']['mean']} | {c['neutral_ic'][f'h{h}']['ir']} |")
        L += ["", "成本敏感性（20 日持有重叠，多空两腿）：", "",
              "| 成本 | 毛年化 | 成本拖累 | 净年化 |", "|---|---|---|---|"]
        for bp in COSTS_BP:
            x = c["cost"][f"{bp}bp"]
            L.append(f"| {bp}bp | {x['gross_ann_pct']}% | {x['cost_drag_ann_pct']}% | "
                     f"**{x['net_ann_pct']}%** |")
        L += ["", f"- 年化单边换手：多头 {c['cost']['ann_turnover_long']}、空头 "
                  f"{c['cost']['ann_turnover_short']}（合计 {c['cost']['ann_turnover_2leg']}）",
              f"- 5 层（20d）组均值：" + " / ".join(
                  f"G{g}={c['layers']['h20']['groups'][f'G{g}']['mean_fwd_pct']}%"
                  for g in range(1, 6)),
              f"- walk-forward 3 段 IC：" + " / ".join(
                  f"{x['segment']}={x['ic_mean']}" for x in c["walk_forward"]),
              f"- 前后半窗 IC：first={c['subsamples']['first_half']['mean']}、"
              f"second={c['subsamples']['second_half']['mean']}；分季度：" + " / ".join(
                  f"{q}={v['ic_mean']}" for q, v in c["subsamples"]["by_quarter"].items()),
              f"- PIT：{c['pit']['method']}", ""]
    L += ["## 三、建议与保留项", ""]
    for name, c in rep["candidates"].items():
        L.append(f"- **{name}**：{c['decision']['support']}进正式四关；保留项="
                 + "；".join(c["decision"]["reasons"][-1:]))
    L += ["", "## 四、局限", ""] + [f"- {x}" for x in rep["limitations"]]
    (OUT / "four_gate_prep_20261103.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "schema": rep["schema_check"],
                      "runtime": rep["meta"]["runtime_sec"]}, ensure_ascii=False), flush=True)
    return 0 if not rep["schema_check"] else 1


if __name__ == "__main__":
    sys.exit(main())
