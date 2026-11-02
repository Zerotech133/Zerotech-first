# -*- coding: utf-8 -*-
"""评分 v0（40/25/15/10/10）离线校准验证 —— 只读，不写生产表。

背景
----
`scripts/score_candidate_pool.py` 的 v0 公式：
    total = 0.40*pct_rank + 0.25*resonance + 0.15*data_quality
            + 0.10*reliability(0.5) - 0.10*risk(0.0)
本脚本用真实历史数据检验：raw_score / pct_rank / resonance / total 与
T+1 / T+5 命中（ret > 0，口径同 scripts/calc_observations.py）的分组单调性。

数据口径（两套，结论强度不同）
------------------------------
1. 实盘口径（exact_v0）：`v2_selection_pool` —— 正是 v0 打分的输入池。
   v0 评分 2026-10-31 才上线，目前仅 3 个池日（10-29/30/31），T+5 标签为 0。
2. 候选池历史代理（candidate_pool_proxy）：`v2_candidate_pool` + `v2_candidate_asset`
   （PREMARKET/SIGNAL，剔除 INTRADAY_5M），按 (market, pool_date, ts_code) 聚合出
   raw=max(score)、resonance=去重策略池数/4、pct_rank=同日同市场百分位、
   dq 用可得的 3 项检查近似，按同一 v0 权重合成 total_proxy。
   **代理池与 v0 实盘池不是同一条数据链**，只能做参考性检验，不能当 v0 校准结论。

标签口径
--------
bar 对齐：取该 symbol 在 v2_bar_daily 中 <= pool_date 的最后一根 bar 作为基准
（兼容周末/节假日生成的池），T+1/T+5 = 该 symbol 后续第 1/5 根 bar 收盘 / 基准收盘 - 1；
hit = return > 0。数据未到期则留空，不硬凑。

统计口径
--------
- 分组单调性：组件分档（连续值五分位；低基数用离散档）后的命中率 / 均值收益趋势；
- 横截面 rank IC：每个 (market, pool_date) 一个 Spearman 单位，跨日汇总并用
  符号翻转检验（避免同日相关性把 p 值做小）；
- 同日头尾差：每日最上 1/3 减最下 1/3 的命中率 / 收益差，再跨日汇总。

样本充足线（脚本内常量，可复核）
--------------------------------
T+5 标签 >= 200 条、覆盖 >= 20 个池日、且 >= 3 个权重分量有可辨识方差，
才认为「可以校准 v0 权重」；否则明确输出「样本不足，不能校准」。

输出
----
C:\\Temp\\score_calibration.json（默认，可用 --json 覆盖）+ stdout 简短结论。
所有 SQL 均为 SELECT，脚本不含任何 INSERT/UPDATE/DELETE。

用法
----
    python scripts/score_calibration.py [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa
from scipy.stats import spearmanr

# ---------------------------------------------------------------------------
# 复用 v0 公式本体，保证校准对象与生产脚本一致（不重写权重/函数）
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
CODE_DIR = SCRIPTS_DIR.parent
for _path in (str(SCRIPTS_DIR), str(CODE_DIR / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import score_candidate_pool as v0  # noqa: E402

from zhice_os_v2.config.settings import get_settings  # noqa: E402

# Windows 终端/计划任务下中文输出：尽量固定 UTF-8，失败不影响主流程
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

W = {
    "pct_rank": v0.W_PCT,
    "resonance": v0.W_RES,
    "data_quality": v0.W_DQ,
    "reliability": v0.W_RELIABILITY,
    "risk_penalty": v0.W_RISK,
}
RELIABILITY_NEUTRAL = v0.RELIABILITY_NEUTRAL
SCORE_VERSION = "v0_20261031"
BAR_MARKETS = ("CN", "HK", "US", "CRYPTO")
HORIZONS = ("t1", "t5")

# 样本充足线
MIN_T5_FOR_CALIBRATION = 200
MIN_DATES_FOR_CALIBRATION = 20
MIN_VARYING_WEIGHT_COMPONENTS = 3
MIN_ROWS_PER_DATE = 8
MIN_GROUP_N = 20

DEFAULT_JSON = r"C:\Temp\score_calibration.json"

V0_FORMULA = ("total = 0.40*pct_rank + 0.25*resonance + 0.15*data_quality "
              "+ 0.10*reliability(0.5) - 0.10*risk(0.0)")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _engine() -> sa.Engine:
    url = get_settings().db_main_url.replace(
        "postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _jsonable(obj):
    """numpy/pandas 标量 -> JSON 友好类型。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if math.isnan(val) else round(val, 6)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, 6)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def _r(value, nd: int = 4):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return None if math.isnan(f) else round(f, nd)


def _spearman(x, y) -> float:
    """返回 Spearman rho；样本不足/常量时返回 nan。"""
    sx = pd.Series(x, dtype="float64").reset_index(drop=True)
    sy = pd.Series(y, dtype="float64").reset_index(drop=True)
    mask = sx.notna() & sy.notna()
    sx, sy = sx[mask], sy[mask]
    if len(sx) < 3 or sx.nunique() < 2 or sy.nunique() < 2:
        return float("nan")
    rho, _ = spearmanr(sx, sy)
    return float(rho) if rho is not None and math.isfinite(float(rho)) else float("nan")


def _pearson(x, y) -> float:
    sx = pd.Series(x, dtype="float64").reset_index(drop=True)
    sy = pd.Series(y, dtype="float64").reset_index(drop=True)
    mask = sx.notna() & sy.notna()
    sx, sy = sx[mask], sy[mask]
    if len(sx) < 3 or sx.std() == 0 or sy.std() == 0:
        return float("nan")
    return float(np.corrcoef(sx, sy)[0, 1])


def _signflip_p(units: list[float], n_perm: int = 4000, seed: int = 7) -> float | None:
    """日期簇级符号翻转检验：均值统计量是否显著偏离 0（不依赖正态假设）。"""
    arr = np.asarray([u for u in units if u is not None and math.isfinite(float(u))],
                     dtype="float64")
    if len(arr) < 3:
        return None
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_perm, len(arr)))
    null = (signs * arr).mean(axis=1)
    observed = abs(float(arr.mean()))
    return float((np.abs(null) >= observed - 1e-12).mean())


_CN_PREFIX = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)


def normalize_code(market: str, code: str) -> str:
    """统一标的代码：CN 的 sh600400/sz000301/bj920028 归一为 600400.SH / 000301.SZ。"""
    code = (code or "").strip()
    if market == "CN":
        m = _CN_PREFIX.match(code)
        if m:
            return f"{m.group(2)}.{m.group(1).upper()}"
    return code


def _read(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(sa.text(sql), conn, params=params or {})


# ---------------------------------------------------------------------------
# 行情 / 标签
# ---------------------------------------------------------------------------
def _load_bars(conn, market_symbols: dict[str, set[str]], start: date) -> pd.DataFrame:
    frames = []
    for market in BAR_MARKETS:
        symbols = sorted(market_symbols.get(market) or [])
        if not symbols:
            continue
        frames.append(_read(
            conn,
            "SELECT market, symbol, trade_date, close FROM v2_bar_daily "
            "WHERE market=:m AND symbol = ANY(:symbols) AND trade_date >= :start",
            {"m": market, "symbols": symbols, "start": start - timedelta(days=30)},
        ))
    if not frames:
        return pd.DataFrame(columns=["market", "symbol", "trade_date", "close"])
    bars = pd.concat(frames, ignore_index=True)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.dropna(subset=["close"])
    bars = bars[bars["close"] > 0]
    return bars.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)


def _bar_presence(conn, market_symbols: dict[str, set[str]]) -> set[tuple[str, str]]:
    present: set[tuple[str, str]] = set()
    for market in BAR_MARKETS:
        symbols = sorted(market_symbols.get(market) or [])
        if not symbols:
            continue
        rows = _read(
            conn,
            "SELECT DISTINCT symbol FROM v2_bar_daily "
            "WHERE market=:m AND symbol = ANY(:symbols)",
            {"m": market, "symbols": symbols},
        )
        present.update((market, s) for s in rows["symbol"].tolist())
    return present


def _build_forward_index(bars: pd.DataFrame) -> dict[tuple[str, str], tuple]:
    """{(market, symbol): (date_ordinals[], ret_t1[], ret_t5[])}。"""
    if bars.empty:
        return {}
    bars = bars.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)
    grouped = bars.groupby(["market", "symbol"], sort=False)
    bars["ret_t1"] = grouped["close"].shift(-1) / bars["close"] - 1.0
    bars["ret_t5"] = grouped["close"].shift(-5) / bars["close"] - 1.0
    index: dict[tuple[str, str], tuple] = {}
    for (market, symbol), group in bars.groupby(["market", "symbol"], sort=False):
        ordinals = np.array([d.toordinal() for d in group["trade_date"]], dtype="int64")
        index[(market, symbol)] = (
            ordinals,
            group["ret_t1"].to_numpy(dtype="float64"),
            group["ret_t5"].to_numpy(dtype="float64"),
        )
    return index


def _attach_forward(df: pd.DataFrame, fwd_index: dict, market_col: str = "market",
                    code_col: str = "code", date_col: str = "pool_date") -> pd.DataFrame:
    """按「<= pool_date 的最后一根 bar」对齐，附加 base_date / lag_days / ret_t1 / ret_t5。"""
    out = df.copy()
    base_dates, lags, ret1, ret5 = [], [], [], []
    for row in out.itertuples(index=False):
        market = getattr(row, market_col)
        code = getattr(row, code_col)
        pool_date = getattr(row, date_col)
        item = fwd_index.get((market, code))
        if item is None:
            base_dates.append(None)
            lags.append(None)
            ret1.append(np.nan)
            ret5.append(np.nan)
            continue
        ordinals, r1, r5 = item
        pos = int(np.searchsorted(ordinals, pool_date.toordinal(), side="right")) - 1
        if pos < 0:
            base_dates.append(None)
            lags.append(None)
            ret1.append(np.nan)
            ret5.append(np.nan)
            continue
        base = date.fromordinal(int(ordinals[pos]))
        base_dates.append(base)
        lags.append((pool_date - base).days)
        ret1.append(float(r1[pos]))
        ret5.append(float(r5[pos]))
    out["base_date"] = base_dates
    out["lag_days"] = lags
    out["ret_t1"] = ret1
    out["ret_t5"] = ret5
    return out


def _label_inventory(df: pd.DataFrame) -> dict:
    inv: dict = {
        "rows_with_base_bar": int(df["base_date"].notna().sum()),
        "rows_without_base_bar": int(df["base_date"].isna().sum()),
        "n_t1": int(df["ret_t1"].notna().sum()),
        "n_t5": int(df["ret_t5"].notna().sum()),
    }
    for h in HORIZONS:
        sub = df[df[f"ret_{h}"].notna()]
        inv[f"n_dates_{h}"] = int(sub["pool_date"].nunique())
        inv[f"n_markets_{h}"] = int(sub["market"].nunique())
        inv[f"hit_rate_{h}"] = _r(sub[f"ret_{h}"].gt(0).mean()) if len(sub) else None
        inv[f"mean_ret_{h}"] = _r(sub[f"ret_{h}"].mean()) if len(sub) else None
        inv[f"median_ret_{h}"] = _r(sub[f"ret_{h}"].median()) if len(sub) else None
    lag = df["lag_days"].dropna()
    if len(lag):
        inv["base_date_lag_days"] = {
            "n": int(len(lag)),
            "share_lag0": _r((lag == 0).mean()),
            "max": int(lag.max()),
        }
    return inv


def _bar_coverage(bars: pd.DataFrame) -> dict:
    out: dict = {}
    for market, group in bars.groupby("market"):
        out[market] = {
            "n_symbols": int(group["symbol"].nunique()),
            "n_trade_days": int(group["trade_date"].nunique()),
            "min_trade_date": str(group["trade_date"].min()),
            "max_trade_date": str(group["trade_date"].max()),
        }
    return out


# ---------------------------------------------------------------------------
# 统计检验（日期簇级，避免同日横截面相关性把 p 值做小）
# ---------------------------------------------------------------------------
def _per_date_ic(df: pd.DataFrame, col: str) -> dict:
    """每个 (market, pool_date) 横截面内 col vs ret 的 Spearman IC，再跨日汇总。"""
    out: dict = {}
    for h in HORIZONS:
        rho_units: list[float] = []
        for _, group in df.groupby(["market", "pool_date"]):
            sample = group[[col, f"ret_{h}"]].dropna()
            if len(sample) < MIN_ROWS_PER_DATE:
                continue
            rho = _spearman(sample[col], sample[f"ret_{h}"])
            if math.isfinite(rho):
                rho_units.append(rho)
        out[h] = {
            "n_cross_sections": len(rho_units),
            "mean_ic": _r(np.mean(rho_units)) if rho_units else None,
            "median_ic": _r(np.median(rho_units)) if rho_units else None,
            "share_positive": _r(np.mean([x > 0 for x in rho_units])) if rho_units else None,
            "signflip_p": _r(_signflip_p(rho_units), 4),
        }
    return out


def _per_date_top_bottom(df: pd.DataFrame, col: str, frac: float = 1 / 3) -> dict:
    """每个横截面内「最高 frac 组 - 最低 frac 组」的命中率/收益差，再跨日汇总。"""
    out: dict = {}
    for h in HORIZONS:
        hit_diffs: list[float] = []
        ret_diffs: list[float] = []
        for _, group in df.groupby(["market", "pool_date"]):
            sample = group[[col, f"ret_{h}"]].dropna()
            if len(sample) < MIN_ROWS_PER_DATE or sample[col].nunique() < 2:
                continue
            rank_pct = sample[col].rank(method="first", pct=True)
            top = sample[rank_pct > 1 - frac]
            bottom = sample[rank_pct <= frac]
            if len(top) < 2 or len(bottom) < 2:
                continue
            hit_diffs.append(float(top[f"ret_{h}"].gt(0).mean()
                                   - bottom[f"ret_{h}"].gt(0).mean()))
            ret_diffs.append(float(top[f"ret_{h}"].mean() - bottom[f"ret_{h}"].mean()))
        out[h] = {
            "n_cross_sections": len(hit_diffs),
            "mean_hit_diff": _r(np.mean(hit_diffs)) if hit_diffs else None,
            "mean_ret_diff": _r(np.mean(ret_diffs)) if ret_diffs else None,
            "share_positive_hit_diff": _r(np.mean([x > 0 for x in hit_diffs]))
            if hit_diffs else None,
            "signflip_p_hit_diff": _r(_signflip_p(hit_diffs), 4),
            "signflip_p_ret_diff": _r(_signflip_p(ret_diffs), 4),
        }
    return out


def _component_report(df: pd.DataFrame, col: str, n_groups: int = 5,
                      restrict_to_horizon: str | None = None) -> dict:
    """分组表 + 单调性 + 横截面 IC + 同日头尾差。"""
    data = df.copy()
    if restrict_to_horizon:
        data = data[data[f"ret_{restrict_to_horizon}"].notna()]
    data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=[col])
    report: dict = {
        "component": col,
        "n": int(len(data)),
        "scope": (f"restricted_to_{restrict_to_horizon}" if restrict_to_horizon else "all_rows"),
    }
    if data.empty:
        return report

    uniq = np.sort(data[col].unique())
    report["n_unique"] = int(len(uniq))
    bucket_labels: list[str]
    if len(uniq) <= max(2, 2 * n_groups):
        bucket = data[col].map({float(v): i for i, v in enumerate(uniq)})
        bucket_labels = [f"{float(v):g}" for v in uniq]
        bucket_mode = "discrete"
    else:
        bucket = pd.qcut(data[col], n_groups, labels=False, duplicates="drop")
        n_buckets = int(bucket.max()) + 1
        bucket_labels = []
        for i in range(n_buckets):
            sub = data.loc[bucket == i, col]
            bucket_labels.append(f"[{sub.min():.4g}, {sub.max():.4g}]")
        bucket_mode = "qcut"
    report["bucket_mode"] = bucket_mode
    report["n_groups_requested"] = n_groups
    report["n_groups_actual"] = int(bucket.max()) + 1
    report["tie_warning"] = bool(bucket_mode == "qcut"
                                 and report["n_groups_actual"] < n_groups)
    data = data.assign(_bucket=bucket)

    buckets = []
    for bidx in sorted(data["_bucket"].dropna().unique()):
        sub = data[data["_bucket"] == bidx]
        entry = {
            "bucket": int(bidx),
            "range": bucket_labels[int(bidx)] if int(bidx) < len(bucket_labels) else None,
            "n": int(len(sub)),
            "col_mean": _r(sub[col].mean()),
        }
        for h in HORIZONS:
            labeled = sub[sub[f"ret_{h}"].notna()]
            entry[f"n_{h}"] = int(len(labeled))
            entry[f"hit_rate_{h}"] = _r(labeled[f"ret_{h}"].gt(0).mean()) if len(labeled) else None
            entry[f"mean_ret_{h}"] = _r(labeled[f"ret_{h}"].mean()) if len(labeled) else None
            entry[f"median_ret_{h}"] = _r(labeled[f"ret_{h}"].median()) if len(labeled) else None
        buckets.append(entry)
    report["buckets"] = buckets

    monotonic: dict = {}
    for h in HORIZONS:
        usable = [b for b in buckets if b["n"] >= MIN_GROUP_N and b[f"hit_rate_{h}"] is not None]
        idx = [b["bucket"] for b in usable]
        rho_hit = _spearman(idx, [b[f"hit_rate_{h}"] for b in usable]) if len(usable) >= 3 else float("nan")
        rho_ret = _spearman(idx, [b[f"mean_ret_{h}"] for b in usable]) if len(usable) >= 3 else float("nan")
        top_bottom_hit = (usable[-1][f"hit_rate_{h}"] - usable[0][f"hit_rate_{h}"]) if len(usable) >= 2 else None
        top_bottom_ret = (usable[-1][f"mean_ret_{h}"] - usable[0][f"mean_ret_{h}"]) if len(usable) >= 2 else None
        monotonic[h] = {
            "n_buckets_used": len(usable),
            "spearman_bucket_vs_hit_rate": _r(rho_hit),
            "spearman_bucket_vs_mean_ret": _r(rho_ret),
            "top_minus_bottom_hit_rate": _r(top_bottom_hit),
            "top_minus_bottom_mean_ret": _r(top_bottom_ret),
            "monotonic": bool(math.isfinite(rho_hit) and abs(rho_hit) >= 0.9) if len(usable) >= 3 else None,
        }
    report["group_monotonicity"] = monotonic
    report["per_date_ic"] = _per_date_ic(data, col)
    report["per_date_top_bottom"] = _per_date_top_bottom(data, col)
    return report


def _binary_component_report(df: pd.DataFrame, col: str, split_value: float) -> dict:
    """二值/低基数分量（如 resonance）：高值组 vs 低值组 + 同日配对差。"""
    data = df.copy()
    data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=[col])
    data["_high"] = data[col] > split_value
    report: dict = {
        "component": col,
        "n": int(len(data)),
        "high_group_rule": f"{col} > {split_value}",
        "n_high": int(data["_high"].sum()),
        "n_low": int((~data["_high"]).sum()),
        "n_unique": int(data[col].nunique()),
        "modal_share": _r(data[col].value_counts(normalize=True).iloc[0]) if len(data) else None,
    }
    for h in HORIZONS:
        labeled = data[data[f"ret_{h}"].notna()]
        high = labeled[labeled["_high"]]
        low = labeled[~labeled["_high"]]
        report[h] = {
            "n_high": int(len(high)),
            "n_low": int(len(low)),
            "hit_rate_high": _r(high[f"ret_{h}"].gt(0).mean()) if len(high) else None,
            "hit_rate_low": _r(low[f"ret_{h}"].gt(0).mean()) if len(low) else None,
            "mean_ret_high": _r(high[f"ret_{h}"].mean()) if len(high) else None,
            "mean_ret_low": _r(low[f"ret_{h}"].mean()) if len(low) else None,
        }
    # 同日配对：同一天内高低两组都出现时，比较命中率/收益
    for h in HORIZONS:
        hit_diffs, ret_diffs = [], []
        for _, group in data.groupby(["market", "pool_date"]):
            sample = group[[col, f"ret_{h}"]].dropna()
            high = sample[sample[col] > split_value]
            low = sample[sample[col] <= split_value]
            if len(high) < 2 or len(low) < 2:
                continue
            hit_diffs.append(float(high[f"ret_{h}"].gt(0).mean()
                                   - low[f"ret_{h}"].gt(0).mean()))
            ret_diffs.append(float(high[f"ret_{h}"].mean() - low[f"ret_{h}"].mean()))
        report[f"paired_date_{h}"] = {
            "n_cross_sections": len(hit_diffs),
            "mean_hit_diff": _r(np.mean(hit_diffs)) if hit_diffs else None,
            "mean_ret_diff": _r(np.mean(ret_diffs)) if ret_diffs else None,
            "share_positive_hit_diff": _r(np.mean([x > 0 for x in hit_diffs]))
            if hit_diffs else None,
            "signflip_p_hit_diff": _r(_signflip_p(hit_diffs), 4),
        }
    return report


def _identifiability(series_map: dict[str, pd.Series]) -> dict:
    """各权重分量在样本中的可辨识性（无方差 = 权重不可校准）。"""
    out: dict = {}
    for name, series in series_map.items():
        vals = pd.to_numeric(series, errors="coerce").dropna()
        n = int(len(vals))
        uniq = int(vals.nunique())
        modal_share = float(vals.value_counts(normalize=True).iloc[0]) if n else None
        std = float(vals.std()) if n > 1 else None
        usable = bool(n >= MIN_T5_FOR_CALIBRATION * 0.5 and uniq >= 3
                      and modal_share is not None and (1 - modal_share) >= 0.05
                      and std is not None and std > 1e-9)
        if n == 0:
            reason = "无样本"
        elif uniq == 1:
            reason = "常量，无区分度"
        elif uniq == 2:
            reason = f"低基数（仅 {uniq} 个取值），无法形成分组梯度"
        elif modal_share is not None and (1 - modal_share) < 0.05:
            reason = f"低区分度（{modal_share:.1%} 为众数）"
        elif std is None or std <= 1e-9:
            reason = "近似常量，无区分度"
        else:
            reason = "有方差"
        out[name] = {
            "n": n,
            "n_unique": uniq,
            "std": _r(std),
            "modal_share": _r(modal_share),
            "usable_for_calibration": usable,
            "reason": reason,
        }
    return out


def _strategy_raw_ic(assets: pd.DataFrame) -> dict:
    """同一策略池内部 raw score 的 rank IC（避免跨策略量纲混杂），按 (market, sv) 汇总。"""
    units: dict[tuple[str, str, str], list[float]] = {}
    for (market, sv, _pool_date), group in assets.groupby(
            ["market", "strategy_version_id", "pool_date"]):
        sample_all = group.dropna(subset=["candidate_score"])
        if len(sample_all) < 6 or sample_all["candidate_score"].nunique() < 2:
            continue
        for h in HORIZONS:
            sample = sample_all[["candidate_score", f"ret_{h}"]].dropna()
            if len(sample) < 6 or sample["candidate_score"].nunique() < 2:
                continue
            rho = _spearman(sample["candidate_score"], sample[f"ret_{h}"])
            if math.isfinite(rho):
                units.setdefault((market, sv, h), []).append(rho)
    out: dict = {"n_unit_min_rows": 6, "by_strategy": {}}
    for (market, sv, h), rho_list in sorted(units.items()):
        key = f"{market}:{sv}"
        entry = out["by_strategy"].setdefault(key, {"market": market, "strategy_version_id": sv})
        entry[h] = {
            "n_units": len(rho_list),
            "mean_ic": _r(np.mean(rho_list)),
            "median_ic": _r(np.median(rho_list)),
            "share_positive": _r(np.mean([x > 0 for x in rho_list])),
            "signflip_p": _r(_signflip_p(rho_list), 4),
        }
    # 按市场汇总
    out["by_market"] = {}
    for market in sorted({k[0] for k in units}):
        entry: dict = {}
        for h in HORIZONS:
            rho_list = [r for (m, _sv, hh), lst in units.items() if m == market and hh == h for r in lst]
            if not rho_list:
                continue
            entry[h] = {
                "n_units": len(rho_list),
                "mean_ic": _r(np.mean(rho_list)),
                "share_positive": _r(np.mean([x > 0 for x in rho_list])),
                "signflip_p": _r(_signflip_p(rho_list), 4),
            }
        out["by_market"][market] = entry
    return out


# ---------------------------------------------------------------------------
# 实盘口径：v2_selection_pool（v0 的真实输入）
# ---------------------------------------------------------------------------
def _market_context(conn, pairs: list[tuple[date, str]]) -> list[dict]:
    """对指定的 (pool_date, market) 计算全市场 T+1 表现，用于解释单日横截面。"""
    out: list[dict] = []
    for pool_date, market in pairs:
        df = _read(
            conn,
            "SELECT symbol, trade_date, close FROM v2_bar_daily "
            "WHERE market=:m AND trade_date BETWEEN :lo AND :hi",
            {"m": market, "lo": pool_date - timedelta(days=10),
             "hi": pool_date + timedelta(days=30)},
        )
        if df.empty:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values(["symbol", "trade_date"])
        df["ret_next"] = df.groupby("symbol")["close"].shift(-1) / df["close"] - 1
        day = df[df["trade_date"] == pool_date]
        valid = day["ret_next"].dropna()
        out.append({
            "pool_date": str(pool_date),
            "market": market,
            "n_symbols": int(len(valid)),
            "market_hit_rate_t1": _r(valid.gt(0).mean()) if len(valid) else None,
            "market_mean_ret_t1": _r(valid.mean()) if len(valid) else None,
            "market_median_ret_t1": _r(valid.median()) if len(valid) else None,
            "note": "全市场口径：当日所有有 bar 标的的 下一根 bar 收益（解释单日横截面，非基准）",
        })
    return out


def _pool_rows_label_breakdown(pool: pd.DataFrame) -> list[dict]:
    out = []
    for (d, m), group in pool.groupby(["pool_date", "market"]):
        n_t1 = int(group["ret_t1"].notna().sum())
        n_t5 = int(group["ret_t5"].notna().sum())
        if n_t1 == 0 and n_t5 == 0:
            continue
        labeled = group[group["ret_t1"].notna()]
        out.append({
            "pool_date": str(d),
            "market": m,
            "n": int(len(group)),
            "n_t1": n_t1,
            "n_t5": n_t5,
            "n_t1_missing_raw_score": int(labeled["raw_score"].isna().sum()),
            "hit_rate_t1": _r(labeled["ret_t1"].gt(0).mean()) if len(labeled) else None,
            "mean_ret_t1": _r(labeled["ret_t1"].mean()) if len(labeled) else None,
        })
    return out


def _exact_v0_section(conn, presence: set[tuple[str, str]],
                      fwd_index: dict) -> tuple[dict, pd.DataFrame]:
    pool = _read(conn, """
        SELECT pool_date, market, code, name, source_strategies, signal_types, max_score
        FROM v2_selection_pool ORDER BY pool_date, market, code
    """)
    if pool.empty:
        return {"error": "v2_selection_pool 为空"}, pool
    pool["pool_date"] = pd.to_datetime(pool["pool_date"]).dt.date
    pool["code"] = [normalize_code(m, c) for m, c in
                    zip(pool["market"], pool["code"], strict=True)]
    pool["raw_score"] = pd.to_numeric(pool["max_score"], errors="coerce")
    pool["name"] = pool["name"].fillna("")
    pool["source_strategies"] = pool["source_strategies"].fillna("")
    pool["signal_types"] = pool["signal_types"].fillna("")

    # v0 组件：逐 (pool_date, market) 横截面算 pct_rank，其余与 score_candidate_pool.py 一致。
    # 注意：生产 v0 对 max_score 缺失按 0 参与 pct_rank（float(x.get("max_score") or 0)），
    # 这里保持一致，否则无法复现实盘排序。
    pool["pct_rank"] = np.nan
    for _, group in pool.groupby(["pool_date", "market"]):
        vals = [float(x) if pd.notna(x) else 0.0 for x in group["raw_score"]]
        pool.loc[group.index, "pct_rank"] = v0.percentile_rank(vals)
    pool["resonance_score"] = [v0._resonance_score(s) for s in pool["source_strategies"]]
    pool["data_quality"] = [
        v0._data_quality(
            {"name": r.name, "signal_types": r.signal_types,
             "max_score": float(r.max_score) if pd.notna(r.max_score) else None,
             "source_strategies": r.source_strategies},
            (r.market, r.code) in presence,
        )
        for r in pool.itertuples(index=False)
    ]
    pool["reliability"] = v0.RELIABILITY_NEUTRAL
    pool["risk_penalty"] = 0.0
    pool["total"] = (W["pct_rank"] * pool["pct_rank"]
                     + W["resonance"] * pool["resonance_score"]
                     + W["data_quality"] * pool["data_quality"]
                     + W["reliability"] * pool["reliability"]
                     - W["risk_penalty"] * pool["risk_penalty"])
    pool = _attach_forward(pool, fwd_index)

    inventory = {
        "source_table": "v2_selection_pool",
        "pool_rows": int(len(pool)),
        "n_pool_dates": int(pool["pool_date"].nunique()),
        "min_pool_date": str(pool["pool_date"].min()),
        "max_pool_date": str(pool["pool_date"].max()),
        "n_max_score_missing": int(pool["raw_score"].isna().sum()),
        "pct_rank_semantics": "max_score 缺失按 0 参与 pct_rank（与 score_candidate_pool.py 一致）",
        "by_date_market": [
            {"pool_date": str(d), "market": m, "n": int(len(g)),
             "n_t1": int(g["ret_t1"].notna().sum()),
             "n_t5": int(g["ret_t5"].notna().sum())}
            for (d, m), g in pool.groupby(["pool_date", "market"])
        ],
        "score_version": SCORE_VERSION,
    }
    components = _identifiability({
        "raw_score": pool["raw_score"],
        "pct_rank": pool["pct_rank"],
        "resonance_score": pool["resonance_score"],
        "data_quality": pool["data_quality"],
        "reliability": pool["reliability"],
        "risk_penalty": pool["risk_penalty"],
    })
    label_breakdown = _pool_rows_label_breakdown(pool)
    section: dict = {
        "description": "实盘口径：v2_selection_pool 是 v0 评分的真实输入池（score_candidate_pool.py）",
        "inventory": inventory,
        "score_components_identifiability": components,
        "labels": _label_inventory(pool),
        "label_breakdown": label_breakdown,
        "market_context": _market_context(
            conn, [(date.fromisoformat(x["pool_date"]), x["market"])
                   for x in label_breakdown]),
    }
    t1_labeled = int(pool["ret_t1"].notna().sum())
    if t1_labeled >= MIN_ROWS_PER_DATE:
        section["component_vs_outcome"] = {
            comp: _component_report(pool, comp, n_groups=3, restrict_to_horizon="t1")
            for comp in ("raw_score", "pct_rank", "total")
        }
        # resonance 在实盘池近似常量（99.8% 为 0.25），单独记录以证明 25% 权重不可辨识
        section["component_vs_outcome"]["resonance_score"] = _binary_component_report(
            pool, "resonance_score", 0.25)
        missing_raw = int(pool.loc[pool["ret_t1"].notna(), "raw_score"].isna().sum())
        section["note_discrete"] = (
            f"T+1 仅 {t1_labeled} 条（{len(label_breakdown)} 个横截面），其中 {missing_raw} 条"
            "缺 max_score（生产 v0 按 0 参与排名）；分组/IC 只能作为单横截面描述，"
            "T+5 无标签，无法评估任何 T+5 单调性。"
        )
    else:
        section["component_vs_outcome"] = {}
        section["note_discrete"] = "T+1 标签不足一个横截面，无法做任何分组/IC 计算。"

    missing: list[str] = []
    if pool["ret_t5"].notna().sum() < MIN_T5_FOR_CALIBRATION:
        missing.append(f"T+5 标签 {int(pool['ret_t5'].notna().sum())} < {MIN_T5_FOR_CALIBRATION}")
    if pool.loc[pool["ret_t5"].notna(), "pool_date"].nunique() < MIN_DATES_FOR_CALIBRATION:
        missing.append(
            f"T+5 覆盖池日 {int(pool.loc[pool['ret_t5'].notna(), 'pool_date'].nunique())} "
            f"< {MIN_DATES_FOR_CALIBRATION}")
    varying = sum(1 for c in components.values() if c["usable_for_calibration"])
    if varying < MIN_VARYING_WEIGHT_COMPONENTS:
        missing.append(
            f"可辨识权重分量仅 {varying} 个 < {MIN_VARYING_WEIGHT_COMPONENTS}"
            "（reliability/risk 为常量，resonance/dq 仅 2 档）")
    section["sufficient_for_v0_calibration"] = {
        "enough": not missing,
        "missing_conditions": missing,
        "thresholds": {
            "min_t5": MIN_T5_FOR_CALIBRATION,
            "min_dates": MIN_DATES_FOR_CALIBRATION,
            "min_varying_components": MIN_VARYING_WEIGHT_COMPONENTS,
        },
    }
    return section, pool


# ---------------------------------------------------------------------------
# 代理口径：候选池历史（v2_candidate_pool + v2_candidate_asset）
# ---------------------------------------------------------------------------
def _proxy_section(conn, presence: set[tuple[str, str]],
                   fwd_index: dict) -> tuple[dict, dict]:
    assets = _read(conn, """
        SELECT p.pool_date, p.market, p.strategy_version_id, p.pool_type, p.pool_id,
               a.ts_code, a.candidate_score, a.candidate_reason
        FROM v2_candidate_asset a
        JOIN v2_candidate_pool p ON p.pool_id = a.pool_id
        WHERE p.pool_type IN ('PREMARKET', 'SIGNAL')
        ORDER BY p.pool_date, p.market, a.ts_code
    """)
    if assets.empty:
        return {"error": "候选池为空"}, {}
    assets["pool_date"] = pd.to_datetime(assets["pool_date"]).dt.date
    assets["candidate_score"] = pd.to_numeric(assets["candidate_score"], errors="coerce")
    assets["ts_code"] = [normalize_code(m, c) for m, c in
                         zip(assets["market"], assets["ts_code"], strict=True)]
    assets["candidate_reason"] = assets["candidate_reason"].fillna("").str.strip()

    # 聚合到 (market, pool_date, code) 横截面；来源数按不同候选池（策略×池型）去重
    proxy = assets.groupby(["market", "pool_date", "ts_code"], as_index=False).agg(
        raw=("candidate_score", "max"),
        n_sources=("pool_id", "nunique"),
        has_reason=("candidate_reason", lambda s: bool((s != "").any())),
    ).rename(columns={"ts_code": "code"})
    proxy["has_bar"] = [(m, c) in presence
                        for m, c in zip(proxy["market"], proxy["code"], strict=True)]
    proxy["pct_rank"] = np.nan
    for _, group in proxy.groupby(["market", "pool_date"]):
        valid = group["raw"].notna()
        if not valid.any():
            continue
        pcts = v0.percentile_rank([float(x) for x in group.loc[valid, "raw"]])
        proxy.loc[group.loc[valid].index, "pct_rank"] = pcts
    proxy["resonance_score"] = np.minimum(1.0, proxy["n_sources"] / 4.0)
    # v0 的 dq 有 5 项检查；候选池历史只有 3 项可得（score/reason/bar），按同样 -0.2/项近似
    proxy["data_quality"] = (1.0 - 0.2 * proxy["raw"].isna().astype(float)
                             - 0.2 * (~proxy["has_reason"]).astype(float)
                             - 0.2 * (~proxy["has_bar"]).astype(float))
    proxy["reliability"] = v0.RELIABILITY_NEUTRAL
    proxy["risk_penalty"] = 0.0
    proxy["total"] = (W["pct_rank"] * proxy["pct_rank"]
                      + W["resonance"] * proxy["resonance_score"]
                      + W["data_quality"] * proxy["data_quality"]
                      + W["reliability"] * proxy["reliability"]
                      - W["risk_penalty"] * proxy["risk_penalty"])
    proxy = _attach_forward(proxy, fwd_index)
    assets = assets.merge(
        proxy[["market", "pool_date", "code", "ret_t1", "ret_t5"]],
        left_on=["market", "pool_date", "ts_code"],
        right_on=["market", "pool_date", "code"], how="left")

    by_market = []
    raw_resolution: dict = {}
    for market, group in proxy.groupby("market"):
        inv = _label_inventory(group)
        by_market.append({"market": market, **inv})
        vals = group["raw"].dropna()
        raw_resolution[market] = {
            "n": int(len(vals)),
            "n_unique": int(vals.nunique()),
            "modal_share": _r(vals.value_counts(normalize=True).iloc[0]) if len(vals) else None,
            "top_values": {str(float(k)): int(v)
                           for k, v in vals.value_counts().head(5).items()},
        }
    components = _identifiability({
        "raw_score": proxy["raw"],
        "pct_rank": proxy["pct_rank"],
        "resonance_score": proxy["resonance_score"],
        "data_quality": proxy["data_quality"],
        "reliability": proxy["reliability"],
        "risk_penalty": proxy["risk_penalty"],
    })
    tp_pct = proxy[["total", "pct_rank"]].dropna()
    res_modal = float(proxy["resonance_score"].value_counts(normalize=True).iloc[0])
    section: dict = {
        "description": (
            "候选池历史代理：v2_candidate_pool/asset（PREMARKET+SIGNAL，剔除 INTRADAY_5M）；"
            "按 (market, pool_date, code) 聚合 max(score)/去重策略数，套 v0 公式。"
            "与 v0 实盘输入链不同（v2_selection_pool 来自 selection_pick），只能作参考。"),
        "n_rows_assets": int(len(assets)),
        "n_rows": int(len(proxy)),
        "n_pool_dates": int(proxy["pool_date"].nunique()),
        "min_pool_date": str(proxy["pool_date"].min()),
        "max_pool_date": str(proxy["pool_date"].max()),
        "by_market": by_market,
        "labels": _label_inventory(proxy),
        "raw_score_resolution": raw_resolution,
        "score_components_identifiability": components,
        "total_proxy_vs_pct_rank": {
            "n": int(len(tp_pct)),
            "spearman": _r(_spearman(tp_pct["total"], tp_pct["pct_rank"])),
            "pearson": _r(_pearson(tp_pct["total"], tp_pct["pct_rank"])),
            "note": "total_proxy 与 pct_rank 近似单调 → 权重结构未被独立检验",
        },
        "raw_score_by_strategy": _strategy_raw_ic(assets),
        "caveats": [
            "候选池原始分是策略内部分，不同策略量纲不同（0~1 / 0~4 / 常量），"
            "跨策略混合 pct_rank 含量纲噪声，与 v0 实盘池的归一化分数不完全等价。",
            "raw_score 高度并列（CN 42.6% 为 1.0；US/HK/CRYPTO 大量常量 0），"
            "连续分档会因并列被合并，单调性结论受并列结构限制。",
            "v0 的 dq 5 项检查中只有 3 项在候选池历史可得，dq 近似为常量（≈1）。",
            "reliability/risk 在 v0 中固定 0.5/0.0，无方差。",
            f"resonance 受策略池数量限制，历史 {res_modal:.1%} 样本为单来源覆盖 → "
            "resonance_score 近似常量 0.25。",
        ],
    }
    section["component_vs_outcome"] = {
        "raw_score_by_market": {
            market: _component_report(group, "raw", n_groups=5)
            for market, group in proxy.groupby("market")
        },
        "pct_rank": _component_report(proxy, "pct_rank", n_groups=5),
        "resonance_score": _binary_component_report(proxy, "resonance_score", 0.25),
        "total_proxy": _component_report(proxy, "total", n_groups=5),
    }
    return section, {"proxy": proxy, "assets": assets, "components": components}


# ---------------------------------------------------------------------------
# 观察表 / verdicts 清单（证明其它数据源也不足）
# ---------------------------------------------------------------------------
def _observation_section(conn) -> dict:
    obs = _read(conn, """
        SELECT strategy_id, signal_date, code, status, ret_t1, ret_t5, ret_t10,
               ret_t20, hit FROM v2_strategy_observation
    """)
    section: dict = {"source_table": "v2_strategy_observation"}
    if obs.empty:
        section["error"] = "观察表为空"
        return section
    section["rows"] = int(len(obs))
    section["n_dates"] = int(pd.to_datetime(obs["signal_date"]).dt.date.nunique())
    section["horizon_coverage"] = {
        f"ret_{h}": int(pd.to_numeric(obs[f"ret_{h}"], errors="coerce").notna().sum())
        for h in ("t1", "t5", "t10", "t20")
    }
    section["hit_count"] = int(pd.to_numeric(obs["hit"], errors="coerce").fillna(0).sum())
    section["status_counts"] = {str(k): int(v) for k, v in obs["status"].value_counts().items()}
    section["strategy_counts"] = {str(k): int(v) for k, v in obs["strategy_id"].value_counts().items()}
    section["sufficient_for_calibration"] = {
        "enough": False,
        "missing_conditions": [
            "观察表无评分字段，无法与 v0 分数关联",
            f"T+5 标签 {section['horizon_coverage']['ret_t5']} < {MIN_T5_FOR_CALIBRATION}",
            f"覆盖池日 {section['n_dates']} < {MIN_DATES_FOR_CALIBRATION}",
        ],
    }
    return section


def _verdict_section(conn) -> dict:
    section: dict = {}
    cards = _read(conn, """
        SELECT pool_date, direction, status, score, rating
        FROM v2_stock_research
    """)
    if not cards.empty:
        cards["score"] = pd.to_numeric(cards["score"], errors="coerce")
        section["stock_research"] = {
            "rows": int(len(cards)),
            "n_dates": int(pd.to_datetime(cards["pool_date"]).dt.date.nunique()),
            "direction_counts": {str(k): int(v) for k, v in cards["direction"].value_counts().items()},
            "status_counts": {str(k): int(v) for k, v in cards["status"].value_counts().items()},
            "score_min": _r(cards["score"].min()),
            "score_max": _r(cards["score"].max()),
            "note": "研究卡 score 为 v1 口径 0~100，与 v0 评分不同源，且无价格后验数据",
        }
    lusu = _read(conn, "SELECT pool_date, verdict FROM v2_lusu_decision")
    if not lusu.empty:
        section["lusu_decision"] = {
            "rows": int(len(lusu)),
            "verdict_counts": {str(k): int(v) for k, v in lusu["verdict"].value_counts().items()},
            "note": "仅决策记录，无 T+1/T+5 收益后验",
        }
    acad1 = _read(conn, "SELECT review_date, verdict FROM v2_acad1_review")
    if not acad1.empty:
        section["acad1_review"] = {
            "rows": int(len(acad1)),
            "verdict_counts": {str(k): int(v) for k, v in acad1["verdict"].value_counts().items()},
            "note": "研究评审记录，无价格后验",
        }
    pool = _read(conn, "SELECT pool_date, count(*) AS n FROM v2_acad1_pool GROUP BY 1 ORDER BY 1")
    if not pool.empty:
        section["acad1_pool"] = {
            "rows": int(pool["n"].sum()),
            "n_dates": int(len(pool)),
            "date_range": [str(pool["pool_date"].min()), str(pool["pool_date"].max())],
            "note": "ACAD1 池对应观察行均为 pending（见观察表），无 T+5 标签",
        }
    section["sufficient_for_calibration"] = {
        "enough": False,
        "missing_conditions": ["verdicts/研究卡均无价格后验，无法构造 T+1/T+5 标签",
                               "分数口径不是 v0"],
    }
    return section


# ---------------------------------------------------------------------------
# 结论
# ---------------------------------------------------------------------------
def _fmt_ic(entry: dict | None, label: str) -> str:
    if not entry:
        return f"{label} 无数据"
    ic = entry.get("mean_ic")
    n = entry.get("n_cross_sections")
    p = entry.get("signflip_p")
    if ic is None:
        return f"{label} 无可用横截面"
    return (f"{label} 日均 IC={ic:+.3f}（{n} 个横截面，"
            f"符号翻转 p={p if p is not None else 'NA'}）")


def _market_context_text(context: list[dict]) -> str:
    if not context:
        return "无"
    return "；".join(
        f"{c['market']} {c['pool_date']} 全市场 T+1 命中率 {c['market_hit_rate_t1']}"
        f"、均值 {c['market_mean_ret_t1']}（{c['n_symbols']} 只）"
        for c in context)


def _build_conclusion(exact: dict, proxy: dict, obs: dict, verdicts: dict) -> dict:
    exact_labels = exact.get("labels") or {}
    proxy_labels = proxy.get("labels") or {}
    exact_ok = bool((exact.get("sufficient_for_v0_calibration") or {}).get("enough"))
    obs_ok = bool((obs.get("sufficient_for_calibration") or {}).get("enough"))
    verdicts_ok = bool((verdicts.get("sufficient_for_calibration") or {}).get("enough"))
    proxy_components = proxy.get("score_components_identifiability") or {}
    proxy_varying = [k for k, v in proxy_components.items() if v.get("usable_for_calibration")]
    proxy_has_evidence = (proxy_labels.get("n_t5") or 0) >= MIN_T5_FOR_CALIBRATION

    verdict = "样本不足，不能校准" if not (exact_ok and obs_ok and verdicts_ok) else "样本充足，可校准"
    breakdown = exact.get("label_breakdown") or []
    breakdown_text = "；".join(
        f"{b['market']} {b['pool_date']} n={b['n']}（T+1 {b['n_t1']} 条，"
        f"命中率 {b['hit_rate_t1']}）"
        for b in breakdown) or "无"
    short = (
        f"v0 实盘池仅 {exact.get('inventory', {}).get('n_pool_dates', 0)} 个池日："
        f"T+1 标签 {exact_labels.get('n_t1', 0)} 条 / T+5 标签 {exact_labels.get('n_t5', 0)} 条"
        f"（{'；'.join((exact.get('sufficient_for_v0_calibration') or {}).get('missing_conditions', []))}）；"
        f"观察表 T+5=0、verdicts 无价格后验。→ {verdict}。"
    )
    if proxy_has_evidence:
        short += (
            f" 候选池历史代理（T+5 {proxy_labels.get('n_t5')} 条 / "
            f"{proxy_labels.get('n_dates_t5')} 个池日，非 v0 输入链）未发现显著正向单调性，"
            "仅作参考、不能当 v0 校准。")
    detail: list[str] = [
        f"实盘口径（v2_selection_pool，v0 真实输入）：{exact.get('inventory', {}).get('pool_rows', 0)} 行 / "
        f"{exact.get('inventory', {}).get('n_pool_dates', 0)} 个池日；"
        f"T+1 可用 {exact_labels.get('n_t1', 0)} 条（{breakdown_text}），"
        f"T+5 可用 {exact_labels.get('n_t5', 0)} 条。v0 评分 2026-10-31 才上线，"
        "行情只到 2026-10-30/31，T+5 尚无到期样本。",
        f"实盘池数据质量：全池 {exact.get('inventory', {}).get('n_max_score_missing', 0)}/"
        f"{exact.get('inventory', {}).get('pool_rows', 0)} 行缺 max_score，"
        f"唯一有 T+1 的横截面中 {sum(b.get('n_t1_missing_raw_score') or 0 for b in breakdown)}"
        f"/{exact_labels.get('n_t1', 0)} 条缺分（生产 v0 按 0 参与 pct_rank）→ "
        "该横截面排序本身受缺分影响，单日结果不能评判 v0。",
        f"单日行情背景：{_market_context_text(exact.get('market_context') or [])}"
        "（单日横截面，不能外推）。",
        f"观察表（v2_strategy_observation）：{obs.get('rows', 0)} 行，"
        f"T+1 可用 {(obs.get('horizon_coverage') or {}).get('ret_t1', 0)} 条，"
        f"T+5 可用 {(obs.get('horizon_coverage') or {}).get('ret_t5', 0)} 条；无评分字段，无法与 v0 关联。",
        f"verdicts/研究卡：v2_stock_research {((verdicts.get('stock_research') or {}).get('rows', 0))} 行、"
        f"v2_lusu_decision {((verdicts.get('lusu_decision') or {}).get('rows', 0))} 行、"
        f"v2_acad1_review {((verdicts.get('acad1_review') or {}).get('rows', 0))} 行；"
        "均无 T+1/T+5 价格后验，且分数不是 v0 口径。",
        "权重分量可辨识性（实盘口径）：仅 "
        + (", ".join(k for k, v in (exact.get('score_components_identifiability') or {}).items()
                     if v.get('usable_for_calibration')) or "无")
        + " 有足够方差；reliability 恒为 0.5、risk 恒为 0，resonance/dq 仅 2 档（低基数） → "
        "即使有标签也无法用分组单调性反推 40/25/15/10/10 权重。",
    ]

    proxy_evidence: dict = {}
    if proxy_has_evidence:
        cv = proxy.get("component_vs_outcome") or {}
        pct_rep = cv.get("pct_rank") or {}
        raw_market = cv.get("raw_score_by_market") or {}
        total_rep = cv.get("total_proxy") or {}
        res_rep = cv.get("resonance_score") or {}
        strat = (proxy.get("raw_score_by_strategy") or {}).get("by_market") or {}
        proxy_evidence = {
            "note": "以下为候选池历史代理检验，仅作参考方向，不能作为 v0 权重校准结论",
            "n_t1": proxy_labels.get("n_t1"),
            "n_t5": proxy_labels.get("n_t5"),
            "n_dates": proxy_labels.get("n_dates_t5"),
            "baseline_hit_rate_t1": proxy_labels.get("hit_rate_t1"),
            "baseline_hit_rate_t5": proxy_labels.get("hit_rate_t5"),
            "pct_rank": {
                "t1": (pct_rep.get("per_date_ic") or {}).get("t1"),
                "t5": (pct_rep.get("per_date_ic") or {}).get("t5"),
                "monotonicity": pct_rep.get("group_monotonicity"),
            },
            "raw_score_by_strategy": strat,
            "raw_score_by_market_group_trend": {
                market: (rep.get("group_monotonicity") or {})
                for market, rep in raw_market.items()
            },
            "resonance": {
                "t1": (res_rep.get("t1") or {}),
                "t5": (res_rep.get("t5") or {}),
                "n_unique": res_rep.get("n_unique"),
                "modal_share": res_rep.get("modal_share"),
            },
            "total_proxy": {
                "t1": (total_rep.get("per_date_ic") or {}).get("t1"),
                "t5": (total_rep.get("per_date_ic") or {}).get("t5"),
                "monotonicity": total_rep.get("group_monotonicity"),
            },
            "varying_components": proxy_varying,
            "important_caveat": (
                "候选池历史只有 pct_rank/raw_score 分量真正有方差；"
                "resonance≈0.25 常量、dq≈1 常量、reliability/risk 固定，"
                "total_proxy 几乎等价于 pct_rank 的单调变换（权重未获独立检验）。"
            ),
        }
        pct_t1 = (pct_rep.get("per_date_ic") or {}).get("t1") or {}
        pct_t5 = (pct_rep.get("per_date_ic") or {}).get("t5") or {}
        res_t5 = res_rep.get("t5") or {}
        detail.append(
            f"候选池历史代理（非 v0 输入链）：T+1 {proxy_labels.get('n_t1', 0)} 条 / "
            f"T+5 {proxy_labels.get('n_t5', 0)} 条 / {proxy_labels.get('n_dates_t5', 0)} 个池日"
            f"（基准命中率 T+1 {proxy_labels.get('hit_rate_t1')} / T+5 {proxy_labels.get('hit_rate_t5')}）："
            + _fmt_ic(pct_t1, "pct_rank T+1")
            + "；" + _fmt_ic(pct_t5, "pct_rank T+5")
            + " → 未发现 v0 排序有显著正向区分度。")
        detail.append(
            "代理分项："
            + "；".join(
                f"{market} raw 分组趋势 T+1={((raw_market.get(market) or {}).get('group_monotonicity') or {}).get('t1', {}).get('spearman_bucket_vs_hit_rate')}"
                f"/T+5={((raw_market.get(market) or {}).get('group_monotonicity') or {}).get('t5', {}).get('spearman_bucket_vs_hit_rate')}"
                for market in raw_market)
            + f"（方向不一致）；resonance 高/低组 T+5 命中 {res_t5.get('hit_rate_high')}/{res_t5.get('hit_rate_low')}，"
            f"样本 {(res_rep.get('modal_share') or 0):.1%} 为单来源覆盖，无法判定 25% 权重。")

    res_modal_share = (((proxy.get("component_vs_outcome") or {})
                        .get("resonance_score") or {}).get("modal_share"))
    recommendations = [
        "不要基于当前数据调整 v0 权重；先积累 v0 实盘池 + T+5 到期标签"
        f"（建议 ≥{MIN_DATES_FOR_CALIBRATION} 个交易日、≥4 市场、≥{MIN_T5_FOR_CALIBRATION} 条 T+5 标签）。",
        "在 v2_selection_pool 落库时同表落 score 组件（pct_rank/resonance/dq/total）或扩展反馈表，"
        "否则后续仍无法做权重校准。",
        "resonance 当前上限 4，但历史实际去重来源数最多 2"
        f"（{(res_modal_share or 0):.1%} 为单来源覆盖），建议改为按实际策略数归一，"
        "或先固定权重、待出现真实多来源样本再定。",
        "data_quality/reliability/risk 在历史数据里是低基数或常量，权重 15/10/10 无法被数据检验，"
        "应先补数据再谈权重。",
        "补 T+5 收益标签落库（v2_strategy_observation 目前 ret_t5 全为 NULL），"
        "并明确定义 T+5 命中口径（现有 hit 只等于 ret_t1>0）。",
    ]
    return {
        "verdict": verdict,
        "short": short,
        "detail": detail,
        "proxy_evidence": proxy_evidence,
        "weight_calibration_possible": False,
        "weight_calibration_reason": (
            "实盘 v0 池样本不足（T+5=0），且 5 个权重分量中仅 pct_rank/raw_score 有可辨识方差；"
            "resonance/dq 低基数、reliability/risk 为常量，无法通过分组单调性反推 40/25/15/10/10。"
        ),
        "thresholds": {
            "min_t5": MIN_T5_FOR_CALIBRATION,
            "min_dates": MIN_DATES_FOR_CALIBRATION,
            "min_varying_components": MIN_VARYING_WEIGHT_COMPONENTS,
        },
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="评分 v0 离线校准验证（只读）")
    parser.add_argument("--json", default=DEFAULT_JSON, help="输出 JSON 路径")
    args = parser.parse_args()

    engine = _engine()
    with engine.connect() as conn:
        try:
            raw_pool = _read(conn, "SELECT market, code FROM v2_selection_pool")
            symbol_map: dict[str, set[str]] = {m: set() for m in BAR_MARKETS}
            for market, code in zip(raw_pool["market"], raw_pool["code"], strict=True):
                symbol_map.setdefault(market, set()).add(normalize_code(market, code))

            raw_assets = _read(conn, """
                SELECT DISTINCT p.market, a.ts_code
                FROM v2_candidate_asset a JOIN v2_candidate_pool p ON p.pool_id = a.pool_id
                WHERE p.pool_type IN ('PREMARKET', 'SIGNAL')
            """)
            for market, code in zip(raw_assets["market"], raw_assets["ts_code"], strict=True):
                symbol_map.setdefault(market, set()).add(normalize_code(market, code))

            presence = _bar_presence(conn, symbol_map)
            date_rows = _read(conn, """
                SELECT min(d) AS lo FROM (
                  SELECT min(pool_date) AS d FROM v2_selection_pool
                  UNION ALL
                  SELECT min(pool_date) AS d FROM v2_candidate_pool
                ) t
            """)
            min_pool_date = pd.to_datetime(date_rows["lo"].iloc[0]).date()
            bars = _load_bars(conn, symbol_map, min_pool_date)
            fwd_index = _build_forward_index(bars)
            bar_coverage = _bar_coverage(bars)

            exact, _ = _exact_v0_section(conn, presence, fwd_index)
            proxy, _ = _proxy_section(conn, presence, fwd_index)
            obs = _observation_section(conn)
            verdicts = _verdict_section(conn)
        finally:
            conn.rollback()

    conclusion = _build_conclusion(exact, proxy, obs, verdicts)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": "scripts/score_calibration.py",
        "mode": "read_only_db (no INSERT/UPDATE/DELETE)",
        "score_version": SCORE_VERSION,
        "weights": {k: round(float(v), 4) for k, v in W.items()},
        "v0_formula": V0_FORMULA,
        "reliability_neutral": RELIABILITY_NEUTRAL,
        "label_definition": (
            "T+1/T+5 = 未来第 1/5 根 bar 收盘 / <=pool_date 最后一根 bar 收盘 - 1；"
            "hit = ret > 0（T+1 口径同 scripts/calc_observations.py）"),
        "methodology": {
            "exact_input": "v2_selection_pool（v0 真实输入）",
            "proxy_input": ("v2_candidate_pool+v2_candidate_asset（PREMARKET/SIGNAL，"
                            "剔除 INTRADAY_5M；与 v0 输入链不同，仅参考）"),
            "tests": [
                "分组单调性：组件分档（连续值五分位；低基数用离散档）后的命中率/均值收益趋势",
                "横截面 rank IC：每个 (market, pool_date) 一个 Spearman 单位，跨日符号翻转检验",
                "同日头尾差：每日最上 1/3 减最下 1/3 的命中率/收益差，跨日符号翻转检验",
                "权重可辨识性：分量方差 < 常量或低基数 → 对应权重不可校准；"
                "usable_for_calibration 只表示该分量有可辨识方差，不代表 T+1/T+5 标签充足",
            ],
            "read_only": True,
        },
        "bar_coverage": bar_coverage,
        "conclusion": conclusion,
        "exact_v0": exact,
        "candidate_pool_proxy": proxy,
        "strategy_observation": obs,
        "verdicts": verdicts,
    }

    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"[score_calibration] saved: {out_path}")
    print(f"[score_calibration] verdict: {conclusion['verdict']}")
    print(f"[score_calibration] {conclusion['short']}")
    for line in conclusion["detail"]:
        print(f"  - {line}")
    proxy_ev = conclusion.get("proxy_evidence") or {}
    if proxy_ev:
        print("[score_calibration] proxy evidence (不能当 v0 校准):")
        print(f"  n_t1={proxy_ev.get('n_t1')} n_t5={proxy_ev.get('n_t5')} "
              f"dates={proxy_ev.get('n_dates')} baseline_hit_t1={proxy_ev.get('baseline_hit_rate_t1')} "
              f"baseline_hit_t5={proxy_ev.get('baseline_hit_rate_t5')}")
        print(f"  pct_rank T+1: {proxy_ev.get('pct_rank', {}).get('t1')}")
        print(f"  pct_rank T+5: {proxy_ev.get('pct_rank', {}).get('t5')}")
        print(f"  total_proxy T+5: {proxy_ev.get('total_proxy', {}).get('t5')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
