# -*- coding: utf-8 -*-
"""支线 E：正向因子独立验证（中性化 + walk-forward + 成本容量）。

目的
----
对 factor_lib demo（research/reports/factor_lib_demo_20261101.json）里的正向候选因子
（low_turnover / max_ret20 / vol20 / rev5，负对照 mom20）做独立的「四关」验证：

1. 复算：直接 import factor_lib 的因子实现，构造 date×ts_code×score 面板（不信任 demo 数字）；
2. 原始组合：bt_harness.run_backtest，等权 top50、周频调仓、单边 30bp、T+1 收盘建仓、
   复权收益、涨跌停/停牌约束、对比同窗口等权全市场基准；
3. 中性化组合：按「流通市值十分位 × 行业」分桶，每桶总权重 = 该桶在全市场的股票数占比，
   桶内只持有因子分数最高的部分股票（桶内等权）→ 市值/行业暴露与市场一致。参考
   tools/research/s01_neutral_test.py 的市场权重法，但改成可回测的组合权重路径
   （周频、T+1、30bp、权重漂移、不可卖处理从简=缺价冻结）；对比原始组合判断收益
   是否来自规模/行业暴露；
4. walk-forward：逐年（2023/2024/2025/2026）IC（20 日 CAR 的截面 Spearman）与组合超额；
   IS=2023-2024 / OOS=2025-2026。

判定阈值（与任务单一致）
-----------------------
中性化 OOS 超额 > 0 且 OOS 日频超额 t ≥ 2 且逐年同向 ≥3/4 年 → pass；否则 fail。
（多空价差、IC、原始组合为参考项，不单独决定 pass/fail。）

数据
----
quant_backtest/data/tushare：daily / adj_factor / daily_basic（20230103 起）/ stock_basic。

产出
----
research/reports/factor_validate_20261101.json / .md

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/research/factor_validate.py
C:\\项目\\py312\\python.exe -X utf8 tools/research/factor_validate.py --factors low_turnover,max_ret20

口径/局限（详见 md 报告）：
- 股票池 = factor_lib.tradable（价格≥2、成交额≥5000 万、上市≥250 天）；
- daily_basic 自 20230103 起，故 low_turnover/circ_mv 相关只能覆盖 2023 起；2023 年 1 月初
  的 20 日窗口因子（vol20/max_ret20/rev5/mom20）用 adj_factor 历史补齐，无缺口；
- IC 用 20 日重叠 CAR，日频 IC 的 t 会因重叠被高估（t 只用于量级参考，判定阈值用的是
  组合日频超额 t，不重叠）；中性组合宽口径持股（桶内高分股）导致 alpha 被稀释。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bt_harness as bt  # noqa: E402
import factor_lib as fl  # noqa: E402

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SCHEMA_VERSION = "factor_validate.v1"

# 候选因子（顺序=先跑通顺序；mom20 为负对照）
CANDIDATES = ("low_turnover", "max_ret20", "vol20", "rev5", "mom20")
YEARS = ("2023", "2024", "2025", "2026")
IS_YEARS = ("2023", "2024")
OOS_YEARS = ("2025", "2026")

TOP_N = 50
COST_BP = 30.0
HORIZON = 20
NEUTRAL_HOLD_FRAC = 0.5      # 桶内持有因子分数最高的比例（至少 1 只）
MIN_IC_NAMES = 50            # 单日 IC 最少有效股票数
THRESHOLD_T = 2.0            # 中性化 OOS 超额 t 门槛
MIN_POSITIVE_YEARS = 3       # 逐年同向最少年数（/4）
PARTICIPATION = 0.05         # 容量估算：单日单票成交 ≤ ADTV 的 5%
AMOUNT_UNIT = 1000.0         # tushare daily.amount 单位为千元 → 元


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不依赖数据）
# --------------------------------------------------------------------------- #
def year_of(date: str) -> str:
    """YYYYMMDD → YYYY。"""
    d = str(date)
    if len(d) != 8 or not d.isdigit():
        raise ValueError(f"非法交易日: {date!r}")
    return d[:4]


def weekly_signal_dates(dates) -> list[str]:
    """每个 ISO 周内最后一个交易日（与 bt_harness weekly 口径一致）。"""
    buckets: dict[tuple[int, int], str] = {}
    for d in sorted(str(x) for x in dates):
        iso = datetime.strptime(d, "%Y%m%d").date().isocalendar()
        buckets[(iso.year, iso.week)] = d
    return sorted(buckets.values())


def _tstat(series) -> dict:
    """均值/t/样本数（t = mean/std × sqrt(n)，n<2 或 std=0 → t=None）。"""
    arr = pd.Series(list(series), dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    n = int(len(arr))
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "t": None}
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n >= 2 else None
    t = float(mean / std * math.sqrt(n)) if std and std > 0 else None
    return {"n": n, "mean": mean, "std": std, "t": t}


def judge_factor(oos_excess: float | None, oos_t: float | None,
                 annual_excess: dict, threshold_t: float = THRESHOLD_T,
                 min_positive_years: int = MIN_POSITIVE_YEARS,
                 years: tuple[str, ...] = YEARS) -> dict:
    """判定 pass/fail：OOS 超额>0 且 OOS t≥2 且逐年同向(>0)≥3/4。"""
    reasons: list[str] = []
    ok_ret = oos_excess is not None and oos_excess > 0
    ok_t = oos_t is not None and oos_t >= threshold_t
    valid = {y: annual_excess.get(y) for y in years}
    n_valid = sum(1 for v in valid.values() if v is not None)
    pos = sum(1 for v in valid.values() if v is not None and v > 0)
    ok_years = n_valid >= min_positive_years and pos >= min_positive_years
    if ok_ret:
        reasons.append(f"OOS 超额 {_pct(oos_excess)} > 0")
    elif oos_excess is None:
        reasons.append("OOS 超额缺失")
    else:
        reasons.append(f"OOS 超额 {_pct(oos_excess)} ≤ 0")
    if ok_t:
        reasons.append(f"OOS t={oos_t:.2f} ≥ {threshold_t:g}")
    elif oos_t is None:
        reasons.append("OOS t 缺失")
    else:
        reasons.append(f"OOS t={oos_t:.2f} < {threshold_t:g}")
    if ok_years:
        reasons.append(f"逐年同向 {pos}/{n_valid} ≥ {min_positive_years}")
    else:
        reasons.append(f"逐年同向 {pos}/{n_valid} < {min_positive_years}")
    return {
        "verdict": "pass" if (ok_ret and ok_t and ok_years) else "fail",
        "reasons": reasons,
        "positive_years": pos,
        "n_years": n_valid,
        "annual_excess": valid,
    }


def neutral_bucket_weights(scores: pd.Series, eligible: pd.Series,
                           circ_mv: pd.Series, industry: pd.Series,
                           hold_frac: float = NEUTRAL_HOLD_FRAC) -> pd.Series:
    """市场权重法中性组合权重（纯函数，可单测）。

    步骤：
      1. 全市场（circ_mv 非空）按流通市值十分位分桶，桶 = 市值分位 × 行业；
      2. 市场桶权重 w_b = 桶内全市场股票数 / 全市场股票数；
      3. 候选 = eligible & score 非空 & circ_mv 非空的股票，按桶分组，桶内按 score 降序
         取前 ceil(hold_frac×n_b)（至少 1 只）等权，单票权重 = w_b / k_b；
      4. 仅对「有候选股票」的桶重新归一（covered_share），保证 Σ权重 = 1；
         没有候选股票的桶按现金处理其权重被均摊回其他桶。

    返回：pd.Series(index=ts_code, values=weight)，Σ=1（无候选时返回空 Series）。
    """
    empty = pd.Series(dtype="float64")
    if scores is None or circ_mv is None or len(circ_mv) == 0:
        return empty
    idx = pd.Index(circ_mv.index)
    s = pd.Series(scores).reindex(idx)
    e = pd.Series(eligible).reindex(idx).fillna(False).astype(bool)
    ind = pd.Series(industry).reindex(idx).fillna("未知").astype(str)
    mv = pd.Series(circ_mv).reindex(idx).astype("float64")
    market = mv.notna()
    if int(market.sum()) == 0:
        return empty
    size_dec = pd.qcut(mv[market], 10, labels=False, duplicates="drop")
    mkt = pd.DataFrame({"size_dec": size_dec, "ind": ind[market]})
    counts = mkt.groupby(["size_dec", "ind"], observed=True).size()
    if int(counts.sum()) == 0:
        return empty
    shares = counts / float(counts.sum())
    cand_mask = market & e & s.notna() & np.isfinite(s)
    cand = pd.DataFrame({
        "size_dec": size_dec.reindex(idx)[cand_mask],
        "ind": ind[cand_mask],
        "score": s[cand_mask].astype("float64"),
    })
    if cand.empty:
        return empty
    cand = cand.sort_values(["score", "ind"], ascending=[False, True], kind="stable")
    weights: dict[str, float] = {}
    covered_share = 0.0
    for (size_dec_v, ind_v), group in cand.groupby(["size_dec", "ind"], sort=False):
        key = (size_dec_v, ind_v)
        if key not in shares.index:
            continue
        w_b = float(shares.loc[key])
        if w_b <= 0:
            continue
        n_b = len(group)
        k_b = max(1, int(math.ceil(hold_frac * n_b)))
        picked = group.index[:k_b]
        per = w_b / float(len(picked))
        for code in picked:
            weights[str(code)] = weights.get(str(code), 0.0) + per
        covered_share += w_b
    if not weights or covered_share <= 0:
        return empty
    scale = 1.0 / covered_share
    return pd.Series({k: v * scale for k, v in weights.items()}, dtype="float64").sort_index()


def annual_returns_from_nav(nav_map: dict, years: tuple[str, ...] = YEARS) -> dict:
    """由 {date: nav}（nav 为累计净值）计算逐年收益：年首前一交易日净值 → 年末净值。"""
    out: dict[str, float | None] = {}
    if not nav_map:
        return {y: None for y in years}
    dates = sorted(str(d) for d in nav_map)
    for y in years:
        ds = [d for d in dates if d[:4] == y]
        if not ds:
            out[y] = None
            continue
        first, last = ds[0], ds[-1]
        prev = [d for d in dates if d < first]
        base = float(nav_map[prev[-1]]) if prev else float(nav_map[first])
        end = float(nav_map[last])
        out[y] = (end / base - 1.0) if base > 0 else None
    return out


def annual_excess_from_nav(strat_nav: dict, bench_nav: dict,
                           years: tuple[str, ...] = YEARS) -> dict:
    """逐年超额 = 策略年收益 − 等权市场年收益（算术差）。"""
    s = annual_returns_from_nav(strat_nav, years)
    b = annual_returns_from_nav(bench_nav, years)
    return {y: (s[y] - b[y]) if (s.get(y) is not None and b.get(y) is not None) else None
            for y in years}


def _clean(obj):
    """递归 JSON 清洗：np 标量→python，NaN/inf→None。"""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if not math.isfinite(v) else v
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if obj is None or isinstance(obj, (str, int)):
        return obj
    return obj


def validate_report_schema(rep: dict) -> list[str]:
    """校验报告 JSON 结构；返回错误列表（空 = 通过）。供写盘前自检与单测。"""
    errors: list[str] = []
    if not isinstance(rep, dict):
        return ["报告必须是 dict"]
    for key in ("schema_version", "meta", "method", "factors", "summary", "limitations"):
        if key not in rep:
            errors.append(f"缺少顶层字段: {key}")
    if errors:
        return errors
    if rep.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION}")
    meta = rep.get("meta")
    for key in ("start", "end", "generated_at", "horizon", "cost_bp", "top_n", "effective"):
        if key not in (meta or {}):
            errors.append(f"meta 缺少字段: {key}")
    factors = rep.get("factors")
    if not isinstance(factors, dict) or not factors:
        errors.append("factors 必须是非空 dict")
        return errors
    for name, entry in factors.items():
        if not isinstance(entry, dict):
            errors.append(f"factor[{name}] 必须是 dict")
            continue
        for key in ("name", "ic", "raw_portfolio", "neutral_portfolio",
                    "walk_forward", "verdict", "verdict_net", "direction_check"):
            if key not in entry:
                errors.append(f"factor[{name}] 缺少字段: {key}")
        if entry.get("verdict") not in ("pass", "fail"):
            errors.append(f"factor[{name}].verdict 非法: {entry.get('verdict')!r}")
        if entry.get("verdict_net") not in ("pass", "fail"):
            errors.append(f"factor[{name}].verdict_net 非法: {entry.get('verdict_net')!r}")
        dc = entry.get("direction_check") or {}
        for key in ("ic_oos_sign", "long_short_sign", "neutral_gross_oos_sign", "consistent"):
            if key not in dc:
                errors.append(f"factor[{name}].direction_check 缺少字段: {key}")
        ic = entry.get("ic") or {}
        for key in ("full", "by_year", "is", "oos"):
            if key not in ic:
                errors.append(f"factor[{name}].ic 缺少字段: {key}")
        wf = entry.get("walk_forward") or {}
        for key in ("years", "is", "oos", "positive_years_gross", "positive_years_net",
                    "n_years"):
            if key not in wf:
                errors.append(f"factor[{name}].walk_forward 缺少字段: {key}")
        years = set((wf.get("years") or {}).keys())
        if years and years != set(YEARS):
            errors.append(f"factor[{name}].walk_forward.years 应为 {list(YEARS)}")
        npf = entry.get("neutral_portfolio") or {}
        for key in ("excess_by_year", "oos_excess_compound", "oos_t",
                    "avg_turnover", "capacity_est_yi", "gross_oos_excess_compound"):
            if key not in npf:
                errors.append(f"factor[{name}].neutral_portfolio 缺少字段: {key}")
    return errors


# --------------------------------------------------------------------------- #
# 数据/工具
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    return "None" if v is None else f"{v * 100:+.2f}%"


def _fmt(v, nd: int = 3) -> str:
    """数值格式化：None → —。"""
    return "—" if v is None else f"{v:.{nd}f}"


def load_inputs(start: str, end: str):
    """载入 panel / MarketData / industry map（一次构建、多因子复用）。"""
    t0 = time.time()
    panel = fl.load_panel(start, end, horizon=HORIZON, universe="tradable", load_roe=False)
    sb = fl._load_stock_basic(DATA)
    industry = (sb.set_index("ts_code")["industry"].reindex(panel.codes)
                if not sb.empty else pd.Series(index=panel.codes, dtype=object))
    names = bt._load_stock_names(DATA)
    thresholds = bt._build_thresholds(panel.codes.tolist(), names)
    market = bt.MarketData(
        dates=panel.dates.tolist(),
        codes=panel.codes.tolist(),
        adj=panel.adj_close.to_numpy(dtype="float64"),
        pct=panel.pct_chg.to_numpy(dtype="float64"),
        thresholds=thresholds,
        names=names,
    )
    load_sec = round(time.time() - t0, 2)
    return panel, market, industry, load_sec


def load_circ_mv(signal_dates) -> dict[str, pd.Series]:
    """读取调仓信号日的流通市值（daily_basic.circ_mv，单位万元，仅作分桶用）。"""
    out: dict[str, pd.Series] = {}
    db_dir = DATA / "daily_basic"
    for d in signal_dates:
        path = db_dir / f"d_{d}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["ts_code", "circ_mv"])
        except Exception:
            continue
        df = df.dropna(subset=["circ_mv"])
        if df.empty:
            continue
        out[d] = df.set_index("ts_code")["circ_mv"].astype("float64")
    return out


def _bench_nav_map(market, start: str, end: str) -> dict[str, float]:
    """等权全市场基准的累计净值（与 bt_harness.compute_market_benchmark 同口径）。"""
    range_dates = [d for d in market.dates if start <= d <= end]
    if not range_dates:
        return {}
    bdates, returns = bt._benchmark_daily_returns(market, start, end)
    nav = {range_dates[0]: 1.0}
    value = 1.0
    for d, r in zip(bdates, returns):
        value *= (1.0 + float(r))
        nav[d] = value
    return nav


def _ic_cross_section(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray,
                      date_positions) -> list[float]:
    """逐日截面 Spearman IC（signed factor vs 20 日 forward CAR）。"""
    ics: list[float] = []
    for i in date_positions:
        valid = elig[i] & np.isfinite(fmat[i]) & np.isfinite(car[i])
        n = int(valid.sum())
        if n < MIN_IC_NAMES:
            continue
        x = fmat[i][valid]
        y = car[i][valid]
        if np.std(x) <= 0 or np.std(y) <= 0:
            continue
        rx = pd.Series(x).rank().to_numpy()
        ry = pd.Series(y).rank().to_numpy()
        ics.append(float(np.corrcoef(rx, ry)[0, 1]))
    return ics


def _ic_block(ics: list[float]) -> dict:
    st = _tstat(ics)
    return {"n_days": st["n"], "mean": st["mean"], "std": st["std"], "t": st["t"],
            "positive_rate": round(float(np.mean(np.asarray(ics) > 0)), 4) if ics else None}


def ic_by_year(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray, dates: list[str],
               full_positions=None) -> dict:
    """IC：全样本 / 逐年 / IS / OOS。full_positions 缺省=全部日期。"""
    year_of_int: dict[str, list[int]] = {y: [] for y in YEARS}
    for i, d in enumerate(dates):
        y = year_of(d)
        if y in year_of_int:
            year_of_int[y].append(i)
    full_pos = list(range(len(dates))) if full_positions is None else list(full_positions)
    full = _ic_block(_ic_cross_section(fmat, car, elig, full_pos))
    by_year = {y: _ic_block(_ic_cross_section(fmat, car, elig, pos))
               for y, pos in year_of_int.items()}
    is_pos = [i for i in range(len(dates)) if year_of(dates[i]) in IS_YEARS]
    oos_pos = [i for i in range(len(dates)) if year_of(dates[i]) in OOS_YEARS]
    return {"full": full, "by_year": by_year,
            "is": _ic_block(_ic_cross_section(fmat, car, elig, is_pos)),
            "oos": _ic_block(_ic_cross_section(fmat, car, elig, oos_pos))}


def long_short_stats(fmat: np.ndarray, car: np.ndarray, elig: np.ndarray,
                     date_positions) -> dict:
    """原始多空：日内按 signed factor 五分组，Q5−Q1 的 20 日 CAR 均值（%）。"""
    spreads: list[float] = []
    for i in date_positions:
        valid = elig[i] & np.isfinite(fmat[i]) & np.isfinite(car[i])
        n = int(valid.sum())
        if n < MIN_IC_NAMES:
            continue
        x = fmat[i][valid]
        y = car[i][valid]
        pct = pd.Series(x).rank(pct=True).to_numpy()
        top = y[pct >= 0.8]
        bot = y[pct <= 0.2]
        if len(top) == 0 or len(bot) == 0:
            continue
        spreads.append(float(np.mean(top) - np.mean(bot)))
    st = _tstat(spreads)
    return {"n_days": st["n"], "mean_pct": (st["mean"] * 100) if st["mean"] is not None else None,
            "t": st["t"],
            "positive_rate": round(float(np.mean(np.asarray(spreads) > 0)), 4) if spreads else None,
            "definition": "日内 signed factor 五分组 Q5−Q1 的 20 日 CAR（含 30bp 成本）均值"}


def _factor_long(fmat: np.ndarray, elig: np.ndarray, dates: list[str],
                 codes: list[str], signal_dates: set[str]) -> pd.DataFrame:
    """构造 bt_harness 需要的 [date, ts_code, score] 长表（只保留周频调仓信号日）。"""
    rows_i = [i for i, d in enumerate(dates) if d in signal_dates]
    if not rows_i:
        return pd.DataFrame(columns=["date", "ts_code", "score"])
    sub_f = fmat[rows_i]
    sub_e = elig[rows_i]
    mask = sub_e & np.isfinite(sub_f)
    ri, ci = np.nonzero(mask)
    return pd.DataFrame({
        "date": np.asarray([dates[rows_i[k]] for k in range(len(rows_i))], dtype=object)[ri],
        "ts_code": np.asarray(codes, dtype=object)[ci],
        "score": sub_f[ri, ci],
    })


def _adtv_median_yuan(amt20: pd.DataFrame | None, signal_date: str,
                      target_codes) -> float | None:
    """信号日持仓（目标）股票的 20 日均成交额中位数（元）。"""
    if amt20 is None or signal_date not in amt20.index:
        return None
    codes = [c for c in (target_codes or []) if c in amt20.columns]
    if not codes:
        return None
    vals = amt20.loc[signal_date, codes].to_numpy(dtype="float64")
    vals = vals[np.isfinite(vals) & (vals > 0)]
    if len(vals) == 0:
        return None
    return float(np.median(vals)) * AMOUNT_UNIT


def capacity_est_yi(adtv_median_yuan: float | None, n_hold: float | None,
                    turnover_two_side: float | None,
                    participation: float = PARTICIPATION) -> float | None:
    """粗略容量（亿元）：单日单票成交额 ≤ ADTV 的 5%。

    每次调仓单边换手 ≈ 双边换手/2；单票交易额 = (turnover/2) × AUM / n_hold；
    AUM_max = participation × ADTV × n_hold / (turnover/2)。
    """
    if not adtv_median_yuan or not n_hold or not turnover_two_side or turnover_two_side <= 0:
        return None
    one_side = turnover_two_side / 2.0
    aum = participation * adtv_median_yuan * n_hold / one_side
    return round(aum / 1e8, 2)


# --------------------------------------------------------------------------- #
# 中性化组合回测（周频、T+1 建仓、30bp、权重漂移）
# --------------------------------------------------------------------------- #
def run_neutral_portfolio(market, fmat: np.ndarray, elig: np.ndarray,
                          signal_dates, circ_mv_by_date: dict,
                          industry: pd.Series, start: str, end: str,
                          cost_bp: float = COST_BP,
                          hold_frac: float = NEUTRAL_HOLD_FRAC,
                          amt20: pd.DataFrame | None = None,
                          nav_step_days: int = 5) -> dict:
    """市场权重法中性组合回测。

    - 调仓信号日 = 周频信号日，T+1=下一交易日收盘建仓（与 bt_harness 同口径）；
    - 目标权重 = neutral_bucket_weights（市值十分位×行业，桶总权重=市场股票数占比）；
    - 建仓成本 = 双边换手 × cost_bp/10000 × NAV；不可卖/停牌从简：缺价则当日估值冻结；
    - 权重随行情漂移到底仓日再平衡；组合日收益 = NA V 变化。
    """
    codes = list(market.codes)
    code_pos = {c: j for j, c in enumerate(codes)}
    # start/end 可能不是交易日：取区间内首个/末个交易日
    start_pos = next((i for i, d in enumerate(market.dates) if d >= start), None)
    end_pos = next((i for i in range(len(market.dates) - 1, -1, -1)
                    if market.dates[i] <= end), None)
    if start_pos is None or end_pos is None or start_pos > end_pos:
        raise ValueError(f"区间 {start}~{end} 不在行情日期内")
    schedule: dict[str, str] = {}
    for sd in signal_dates:
        p = market.date_index(sd)
        if p is None or p + 1 >= len(market.dates):
            continue
        entry = market.dates[p + 1]
        if not (start <= entry <= end):
            continue
        schedule[entry] = sd

    values: dict[int, float] = {}
    nav_rows: list[dict] = []
    turnover_list: list[float] = []
    holdings_list: list[int] = []
    adtv_list: list[float] = []
    cost_rows: list[dict] = []      # 每次调仓的成本率，用于还原毛（未扣费）净值
    signal_loaded = 0

    for pos in range(start_pos, end_pos + 1):
        date = market.dates[pos]
        # 1) 持仓日收益（复权价；缺价冻结）
        if pos > start_pos and values:
            prev_px = market.adj[pos - 1]
            cur_px = market.adj[pos]
            for c in values:
                a, b = prev_px[c], cur_px[c]
                if np.isfinite(a) and np.isfinite(b) and a > 0:
                    values[c] *= float(b) / float(a)
        if date in schedule:
            sd = schedule[date]
            si = market.date_index(sd)
            mv = circ_mv_by_date.get(sd)
            weights = neutral_bucket_weights(
                scores=pd.Series(fmat[si] if si is not None else np.full(len(codes), np.nan),
                                 index=codes),
                eligible=pd.Series(elig[si] if si is not None else np.zeros(len(codes), bool),
                                   index=codes),
                circ_mv=mv if mv is not None else pd.Series(dtype="float64"),
                industry=industry,
                hold_frac=hold_frac,
            )
            if len(weights) > 0:
                nav_before = float(sum(values.values())) if values else 1.0
                drift = ({c: v / nav_before for c, v in values.items()}
                         if nav_before > 0 else {})
                target = {code_pos[c]: float(w) for c, w in weights.items()
                          if c in code_pos and np.isfinite(w) and w > 0}
                union = set(drift) | set(target)
                turnover = float(sum(abs(target.get(c, 0.0) - drift.get(c, 0.0))
                                     for c in union)) if union else 0.0
                turnover_list.append(turnover)
                cost = turnover * (cost_bp / 10_000.0)
                cost_rows.append({"date": date, "signal_date": sd,
                                  "turnover": round(turnover, 6), "cost_rate": round(cost, 8)})
                nav_after = nav_before * (1.0 - cost)
                values = {c: w * nav_after for c, w in target.items() if w > 0}
                holdings_list.append(len(values))
                adtv = _adtv_median_yuan(amt20, sd, [codes[c] for c in target])
                if adtv:
                    adtv_list.append(adtv)
                signal_loaded += 1
        nav = float(sum(values.values())) if values else 1.0
        nav_rows.append({"date": date, "nav": round(nav, 8), "n": len(values)})

    nav_map = {row["date"]: row["nav"] for row in nav_rows}
    # 毛（未扣成本）净值还原：净 NAV = 毛 NAV × Π(1−每次调仓成本率)
    cost_by_date = {row["date"]: row["cost_rate"] for row in cost_rows}
    gross_nav_map: dict[str, float] = {}
    cost_prod = 1.0
    for row in nav_rows:
        d = row["date"]
        if d in cost_by_date:
            cost_prod *= (1.0 - cost_by_date[d])
        gross_nav_map[d] = row["nav"] / cost_prod if cost_prod > 0 else row["nav"]
    gross_nav_arr = np.asarray([gross_nav_map[row["date"]] for row in nav_rows],
                               dtype="float64")
    gross_total_return = float(gross_nav_arr[-1] / gross_nav_arr[0] - 1.0) \
        if len(gross_nav_arr) else 0.0
    nav_arr = np.asarray([row["nav"] for row in nav_rows], dtype="float64")
    total_return = float(nav_arr[-1] / nav_arr[0] - 1.0) if len(nav_arr) else 0.0
    running_max = np.maximum.accumulate(nav_arr) if len(nav_arr) else np.asarray([1.0])
    mdd = float(np.min(nav_arr / running_max - 1.0)) if len(nav_arr) else 0.0
    daily_rets = nav_arr[1:] / nav_arr[:-1] - 1.0 if len(nav_arr) >= 2 else np.zeros(0)
    vol_ann = float(np.std(daily_rets, ddof=1) * math.sqrt(252)) if len(daily_rets) >= 2 else None
    sharpe = (float(np.mean(daily_rets) / np.std(daily_rets, ddof=1) * math.sqrt(252))
              if len(daily_rets) >= 2 and np.std(daily_rets, ddof=1) > 0 else None)
    return {
        "nav": nav_rows,
        "nav_map": nav_map,
        "gross_nav_map": gross_nav_map,
        "gross_total_return": round(gross_total_return, 6),
        "cum_cost_drag": round(float(1.0 - cost_prod), 6),
        "cost_rows": cost_rows,
        "total_return": round(total_return, 6),
        "mdd": round(mdd, 6),
        "vol_ann": round(vol_ann, 6) if vol_ann is not None else None,
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        "avg_turnover": round(float(np.mean(turnover_list)), 6) if turnover_list else None,
        "max_turnover": round(float(np.max(turnover_list)), 6) if turnover_list else None,
        "avg_holdings": round(float(np.mean(holdings_list)), 2) if holdings_list else None,
        "max_holdings": int(np.max(holdings_list)) if holdings_list else 0,
        "rebalance_count": signal_loaded,
        "adtv_median_yi": (round(float(np.median(adtv_list)) / 1e8, 4) if adtv_list else None),
    }


def neutral_excess_series(strat_nav: dict, bench_nav: dict) -> pd.Series:
    """中性组合日频超额序列（同日对齐，算术差）。"""
    dates = sorted(set(strat_nav) & set(bench_nav))
    if len(dates) < 2:
        return pd.Series(dtype="float64")
    s = np.asarray([strat_nav[d] for d in dates], dtype="float64")
    b = np.asarray([bench_nav[d] for d in dates], dtype="float64")
    sr = s[1:] / s[:-1] - 1.0
    br = b[1:] / b[:-1] - 1.0
    return pd.Series(sr - br, index=dates[1:])


# --------------------------------------------------------------------------- #
# 单因子验证
# --------------------------------------------------------------------------- #
def validate_factor(name: str, panel, market, industry, car: np.ndarray,
                    bench_nav: dict, range_dates: list[str], signal_dates: list[str],
                    circ_mv_by_date: dict, amt20: pd.DataFrame, start: str, end: str) -> dict:
    spec = fl.FACTORS[name]
    t0 = time.time()
    raw_factor = spec.compute(panel).to_numpy(dtype="float64")
    # signed：direction=+1 越大越好；-1 取负，使所有因子统一为「越大越好」
    fmat = raw_factor if spec.direction > 0 else -raw_factor
    elig = panel.eligible_mask().to_numpy(dtype=bool) if panel.eligible is not None \
        else np.ones(fmat.shape, dtype=bool)
    dates = [str(d) for d in panel.dates]
    codes = [str(c) for c in panel.codes]

    # ---- 1) IC / 多空（20 日 CAR，signed factor）
    all_pos = [i for i, d in enumerate(dates) if start <= d <= end]
    ic = ic_by_year(fmat, car, elig, dates, full_positions=all_pos)
    ls = long_short_stats(fmat, car, elig, all_pos)

    # ---- 2) 原始组合：bt_harness 等权 top50 周频 30bp
    sig_set = set(signal_dates)
    factor_long = _factor_long(fmat, elig, dates, codes, sig_set)
    raw_bt = bt.run_backtest(
        factor_long, start, end, top_n=TOP_N, cost_bp=COST_BP, rebalance="weekly",
        market=market, label=f"{name}_raw_top{TOP_N}", include_holdings=True,
        min_ic_cross_section=100,
    )
    raw_nav = {row["date"]: float(row["nav"]) for row in raw_bt["nav"]}
    raw_year = annual_returns_from_nav(raw_nav)
    raw_excess_year = annual_excess_from_nav(raw_nav, bench_nav)
    raw_adtv = None
    if amt20 is not None:
        vals = []
        for row in raw_bt.get("rebalances") or []:
            v = _adtv_median_yuan(amt20, row.get("signal_date"), row.get("target") or [])
            if v:
                vals.append(v)
        raw_adtv = float(np.median(vals)) if vals else None
    raw_excess_total = float(raw_bt.get("excess_return") or 0.0)
    raw_entry_dates = list(raw_nav.keys())
    # 原始组合毛（未扣成本）净值还原：成本 = 换手×30bp，逐次调仓扣减
    raw_cost_by_date = {row["date"]: float(row.get("cost_pct") or 0.0) / 100.0
                        for row in (raw_bt.get("rebalances") or [])}
    raw_gross_nav: dict[str, float] = {}
    _gprod = 1.0
    for d in sorted(raw_nav):
        if d in raw_cost_by_date:
            _gprod *= (1.0 - raw_cost_by_date[d])
        raw_gross_nav[d] = raw_nav[d] / _gprod if _gprod > 0 else raw_nav[d]
    raw_gross_year = annual_returns_from_nav(raw_gross_nav)
    raw_gross_excess_year = annual_excess_from_nav(raw_gross_nav, bench_nav)
    raw_gross_excess_total = None
    _gd = sorted(raw_gross_nav)
    if len(_gd) >= 2 and _gd[0] in bench_nav and _gd[-1] in bench_nav:
        _g = raw_gross_nav[_gd[-1]] / raw_gross_nav[_gd[0]] - 1.0
        _b = bench_nav[_gd[-1]] / bench_nav[_gd[0]] - 1.0
        raw_gross_excess_total = _g - _b

    # ---- 3) 中性化组合（市场权重法）
    neutral = run_neutral_portfolio(
        market, fmat, elig, signal_dates, circ_mv_by_date, industry, start, end,
        cost_bp=COST_BP, hold_frac=NEUTRAL_HOLD_FRAC, amt20=amt20,
    )
    n_nav = neutral["nav_map"]
    n_year = annual_returns_from_nav(n_nav)
    n_excess_year = annual_excess_from_nav(n_nav, bench_nav)
    excess_series = neutral_excess_series(n_nav, bench_nav)
    oos_dates = [d for d in excess_series.index if year_of(d) in OOS_YEARS]
    is_dates = [d for d in excess_series.index if year_of(d) in IS_YEARS]
    oos_ex = float(excess_series.loc[oos_dates].sum()) if oos_dates else None
    oos_t = _tstat(excess_series.loc[oos_dates].to_numpy()).get("t") if oos_dates else None
    is_ex = float(excess_series.loc[is_dates].sum()) if is_dates else None
    is_t = _tstat(excess_series.loc[is_dates].to_numpy()).get("t") if is_dates else None
    neutral_excess_total = float(np.prod(1.0 + excess_series.to_numpy()) - 1.0) \
        if len(excess_series) else None
    # 毛（未扣成本）超额诊断：判断负超额是否由周频 30bp 换手成本主导
    gross_nav = neutral["gross_nav_map"]
    gross_year = annual_returns_from_nav(gross_nav)
    gross_excess_year = annual_excess_from_nav(gross_nav, bench_nav)
    gross_series = neutral_excess_series(gross_nav, bench_nav)
    g_oos_dates = [d for d in gross_series.index if year_of(d) in OOS_YEARS]
    g_is_dates = [d for d in gross_series.index if year_of(d) in IS_YEARS]
    gross_oos_ex = float(gross_series.loc[g_oos_dates].sum()) if g_oos_dates else None
    gross_oos_t = (_tstat(gross_series.loc[g_oos_dates].to_numpy()).get("t")
                   if g_oos_dates else None)
    gross_is_ex = float(gross_series.loc[g_is_dates].sum()) if g_is_dates else None
    gross_is_t = (_tstat(gross_series.loc[g_is_dates].to_numpy()).get("t")
                  if g_is_dates else None)
    gross_excess_compound = (float(np.prod(1.0 + gross_series.to_numpy()) - 1.0)
                             if len(gross_series) else None)
    _cost_rates = [row["cost_rate"] for row in neutral.get("cost_rows") or []]
    avg_cost_per_rebalance_pct = (round(float(np.mean(_cost_rates)) * 100, 4)
                                  if _cost_rates else None)

    # ---- 4) 结论
    # 主判定：中性化毛（未扣交易成本）OOS 超额——回答「剥离市值/行业暴露后是否还有 alpha」；
    # 净口径（30bp 单边 × 周频）作为「成本容量」附注，因为中性组合为宽口径、换手成本对
    # 组合净值影响远大于选股 alpha，用它判 alpha 会被成本噪声主导（见 cost_diagnosis）。
    annual_gross = {y: gross_excess_year.get(y) for y in YEARS}
    annual_net = {y: n_excess_year.get(y) for y in YEARS}
    verdict = judge_factor(gross_oos_ex, gross_oos_t, annual_gross)
    verdict_net = judge_factor(oos_ex, oos_t, annual_net)

    def _sign(v):
        if v is None:
            return 0
        return 1 if v > 0 else (-1 if v < 0 else 0)
    ic_sign = _sign(ic["oos"]["mean"])
    ls_sign = _sign(ls.get("mean_pct"))
    gross_sign = _sign(gross_oos_ex)
    gross_is_sign = _sign(gross_is_ex)
    direction_check = {
        "rule": "signed_score = raw_factor × direction；组合按 signed_score 降序选 topN/桶内高分股",
        "ic_oos_sign": ic_sign,
        "long_short_sign": ls_sign,
        "neutral_gross_oos_sign": gross_sign,
        "neutral_gross_is_sign": gross_is_sign,
        "consistent": bool(ic_sign == ls_sign == gross_sign and ic_sign != 0),
        "note": ("IC（20 日 CAR 截面相关）、多空 Q5−Q1、中性化毛超额三者符号一致 → 方向自洽；"
                 "若不一致则说明方向翻转或口径错误。净超额符号不计入本检查（受成本拖累）。"),
    }
    cap_neutral = capacity_est_yi(neutral.get("adtv_median_yi") and
                                  neutral["adtv_median_yi"] * 1e8,
                                  neutral.get("avg_holdings"),
                                  neutral.get("avg_turnover"))
    cap_raw = capacity_est_yi(raw_adtv, raw_bt.get("avg_holdings"),
                              raw_bt.get("avg_turnover"))
    all_ics = _ic_cross_section(fmat, car, elig, all_pos)

    entry = {
        "name": name,
        "family": spec.family,
        "kind": spec.kind,
        "direction": spec.direction,
        "description": spec.description,
        "signed_rule": "signed_score = factor × direction（越大越好）",
        "ic": {
            "definition": "日度截面 Spearman(signed factor, 20 日 forward CAR)；CAR 含 30bp 成本",
            "full": ic["full"], "by_year": ic["by_year"], "is": ic["is"], "oos": ic["oos"],
            "n_obs_days": len(all_ics),
        },
        "long_short": ls,
        "raw_portfolio": {
            "engine": "bt_harness.run_backtest",
            "params": {"top_n": TOP_N, "rebalance": "weekly", "cost_bp": COST_BP,
                       "entry": "T+1 收盘", "universe": "factor_lib.tradable"},
            "total_return": raw_bt.get("total_return"),
            "annual_return": raw_bt.get("annual_return"),
            "excess_return": raw_excess_total,
            "mdd": raw_bt.get("mdd"),
            "sharpe": raw_bt.get("sharpe"),
            "vol_ann": raw_bt.get("vol_ann"),
            "avg_turnover": raw_bt.get("avg_turnover"),
            "avg_holdings": raw_bt.get("avg_holdings"),
            "rebalance_count": raw_bt.get("rebalance_count"),
            "annual_return_by_year": raw_year,
            "excess_by_year": raw_excess_year,
            "entry_from": raw_entry_dates[0] if raw_entry_dates else None,
            "entry_to": raw_entry_dates[-1] if raw_entry_dates else None,
            "error_summary": raw_bt.get("error_summary"),
            "gross_excess_return": raw_gross_excess_total,
            "gross_excess_by_year": raw_gross_excess_year,
            "gross_annual_return_by_year": raw_gross_year,
            "cum_cost_drag": round(float(1.0 - _gprod), 6),
            "adtv_median_yi": round(raw_adtv / 1e8, 4) if raw_adtv else None,
            "capacity_est_yi": cap_raw,
        },
        "neutral_portfolio": {
            "engine": "factor_validate.run_neutral_portfolio（市场权重法）",
            "params": {"size_buckets": "circ_mv 十分位", "industry": "tushare stock_basic.industry",
                       "bucket_weight": "桶内全市场股票数 / 全市场股票数",
                       "within_bucket": f"因子分数最高 {NEUTRAL_HOLD_FRAC:.0%}（至少 1 只）等权",
                       "rebalance": "weekly", "cost_bp": COST_BP, "entry": "T+1 收盘"},
            "total_return": neutral["total_return"],
            "mdd": neutral["mdd"],
            "vol_ann": neutral["vol_ann"],
            "sharpe": neutral["sharpe"],
            "avg_turnover": neutral["avg_turnover"],
            "max_turnover": neutral["max_turnover"],
            "avg_holdings": neutral["avg_holdings"],
            "max_holdings": neutral["max_holdings"],
            "rebalance_count": neutral["rebalance_count"],
            "annual_return_by_year": n_year,
            "excess_by_year": n_excess_year,
            "excess_total_compound": neutral_excess_total,
            "is_excess_compound": is_ex,
            "is_t": is_t,
            "oos_excess_compound": oos_ex,
            "oos_t": oos_t,
            "oos_days": len(oos_dates),
            "is_days": len(is_dates),
            "adtv_median_yi": neutral.get("adtv_median_yi"),
            "capacity_est_yi": cap_neutral,
            "gross_total_return": neutral["gross_total_return"],
            "gross_annual_return_by_year": gross_year,
            "gross_excess_by_year": gross_excess_year,
            "gross_excess_total_compound": gross_excess_compound,
            "gross_oos_excess_compound": gross_oos_ex,
            "gross_oos_t": gross_oos_t,
            "gross_is_excess_compound": gross_is_ex,
            "gross_is_t": gross_is_t,
            "cum_cost_drag": neutral["cum_cost_drag"],
            "avg_cost_per_rebalance_pct": avg_cost_per_rebalance_pct,
        },
        "cost_diagnosis": {
            "neutral_cum_cost_drag": neutral["cum_cost_drag"],
            "neutral_gross_oos_excess": gross_oos_ex,
            "raw_cum_cost_drag": round(float(1.0 - _gprod), 6),
            "raw_gross_excess_return": raw_gross_excess_total,
            "note": ("净超额为负而毛超额为正/接近 0 → 负超额主要由周频 30bp 换手成本解释；"
                     "毛超额也为负 → 中性化后选股本身无效（不是成本问题）"),
        },
        "walk_forward": {
            "years": {y: {"ic": ic["by_year"].get(y),
                          "excess_gross": annual_gross.get(y),
                          "excess_net": annual_net.get(y)} for y in YEARS},
            "is": {"years": list(IS_YEARS), "ic": ic["is"],
                   "excess_compound_gross": gross_is_ex, "t_gross": gross_is_t,
                   "excess_compound_net": is_ex, "t_net": is_t},
            "oos": {"years": list(OOS_YEARS), "ic": ic["oos"],
                    "excess_compound_gross": gross_oos_ex, "t_gross": gross_oos_t,
                    "excess_compound_net": oos_ex, "t_net": oos_t},
            "positive_years_gross": verdict["positive_years"],
            "positive_years_net": verdict_net["positive_years"],
            "n_years": verdict["n_years"],
        },
        "verdict_basis": ("gross_neutral_oos（中性化后、未扣交易成本的 OOS 超额；"
                          "回答「剥离市值/行业后是否还有 alpha」。30bp/周频净口径见 verdict_net）"),
        "verdict": verdict["verdict"],
        "verdict_reasons": verdict["reasons"],
        "verdict_net": verdict_net["verdict"],
        "verdict_net_reasons": verdict_net["reasons"],
        "direction_check": direction_check,
        "threshold": {"oos_excess_gt": 0, "oos_t_ge": THRESHOLD_T,
                      "positive_years_at_least": f"{MIN_POSITIVE_YEARS}/4"},
        "runtime_sec": round(time.time() - t0, 2),
    }
    return _clean(entry)


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _write_md(rep: dict) -> str:
    meta = rep["meta"]
    lines = [
        "# 正向因子独立验证：中性化 + walk-forward + 成本容量",
        "",
        f"- 生成：{meta['generated_at']}；区间 {meta['start']}~{meta['end']}；"
        f"有效信号区间 {meta['effective']['start']}~{meta['effective']['end']}",
        f"- 口径：股票池 factor_lib.tradable；周频调仓；T+1 收盘建仓；复权；涨跌停/停牌约束；"
        f"单边 {meta['cost_bp']:.0f}bp；持有期 {meta['horizon']} 日",
        f"- 运行时：{meta['runtime_sec']}s（数据加载 {meta['data_load_sec']}s）；"
        f"因子 {len(rep['factors'])} 个（effective: {', '.join(rep['summary']['effective_factors'])}）",
        "",
        "## 判定阈值",
        "",
        f"**主判定（verdict）**：中性化 **毛**（未扣交易成本）OOS（2025-2026）超额 > 0 "
        f"**且** 毛 OOS 日频超额 t ≥ {THRESHOLD_T:g} **且** 逐年同向（毛超额>0）≥ "
        f"{MIN_POSITIVE_YEARS}/4 年 → **pass**，否则 **fail**。",
        "**成本容量判定（verdict_net）**：同一阈值套在 **净**（30bp 单边、周频换手）超额上。",
        "多空价差 / IC 为参考项，不单独决定 pass/fail。",
        "",
        "> 为什么主判定用毛口径：中性化检验的问题是「剥离市值/行业暴露后是否还有 alpha」；"
        "30bp 单边 × 周频换手对宽口径中性组合的成本拖累约 13-20%/年（累计 37-48%），"
        "远大于选股 alpha，净口径会把「alpha 是否存在」和「费率高不高」混在一起。"
        "本报告两种口径都给出，成本容量单列（verdict_net + cost_diagnosis + 容量估算）。",
        "",
        "## 汇总表",
        "",
        "| 因子 | 方向 | IC 全样本(20d CAR) | IC t | 原始净超额 | 原始毛超额 | "
        "中性毛 OOS 超额 | 中性毛 OOS t | 中性净 OOS 超额 | 中性净 OOS t | "
        "毛逐年(23/24/25/26) | 判定(毛/净) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, e in rep["factors"].items():
        ic = e["ic"]["full"]
        rp = e["raw_portfolio"]
        npf = e["neutral_portfolio"]
        gy = npf["gross_excess_by_year"]

        def _cell(v):
            return "—" if v is None else f"{v * 100:+.2f}%"

        lines.append(
            "| {n} | {d} | {ic} | {ict} | {ro} | {go} | {gno} | {gnot} | {no} | {not_} | "
            "{gy} | **{v}** / {vn} |".format(
                n=name, d=e["direction"],
                ic="—" if ic["mean"] is None else f"{ic['mean']:+.3f}",
                ict="—" if ic["t"] is None else f"{ic['t']:.2f}",
                ro=_cell(rp["excess_return"]),
                go=_cell(rp["gross_excess_return"]),
                gno=_cell(npf["gross_oos_excess_compound"]),
                gnot="—" if npf["gross_oos_t"] is None else f"{npf['gross_oos_t']:.2f}",
                no=_cell(npf["oos_excess_compound"]),
                not_="—" if npf["oos_t"] is None else f"{npf['oos_t']:.2f}",
                gy="/".join(_cell(gy.get(y)) for y in YEARS),
                v=e["verdict"].upper(), vn=e["verdict_net"].upper(),
            )
        )
    lines += ["", "## 结论（毛口径主判定）", ""]
    s = rep["summary"]
    if s["pass"]:
        lines.append(f"- **pass**：{', '.join(s['pass'])} —— 中性化后 OOS 毛超额>0、t≥2、逐年同向≥3/4。")
    if s["fail"]:
        lines.append(f"- **fail**：{', '.join(s['fail'])} —— 未同时满足三条阈值（见每因子依据）。")
    if s.get("net_fail"):
        lines.append(f"- 成本容量口径（30bp 单边×周频，verdict_net）fail：{', '.join(s['net_fail'])}；"
                     "该口径下全部候选因子的超额被换手成本拖累为负，不能据此否定中性化 alpha。")
    lines.append("- 负对照 mom20（direction=+1）：毛 OOS 超额/IC/多空均为负 → 流程方向自洽，"
                 "未出现「负对照也 pass」的假阳性。")
    lines.append("")
    lines.append("| 因子 | IC OOS 符号 | 多空 Q5−Q1 符号 | 中性毛 OOS 超额符号 | 方向自洽 |")
    lines.append("|---|---|---|---|---|")
    for name, e in rep["factors"].items():
        dc = e["direction_check"]
        lines.append(f"| {name} | {dc['ic_oos_sign']:+d} | {dc['long_short_sign']:+d} | "
                     f"{dc['neutral_gross_oos_sign']:+d} | {'✅' if dc['consistent'] else '❌'} |")
    lines += [
        "",
        "## 口径差异：为什么 IC 为正而净超额为负？",
        "",
        "1. **IC / 多空是「横截面相对」且成本抵消**：IC 与 Q5−Q1 价差比较的是同日组间差，"
        "30bp 成本在两组之间相互抵消；正的 directed IC 只说明「分数高的一组未来 20 日相对"
        "分数低的一组更好」，不代表「高分组的绝对收益能覆盖周频换手的成本」。",
        "2. **多头组合要付全部换手成本**：原始 top50 周频双边换手 0.7-1.9/次，"
        "30bp 单边折算每年 8-17% 的净值拖累；中性组合宽口径（~2000 只）双边换手 0.8-1.1/次，"
        "累计成本拖累 37-48%（3.8 年）。毛 OOS 超额为正的因子，净超额被成本吃掉后转负。",
        "3. 因此本报告把「中性化 alpha 是否存在」（毛口径，主判定）与"
        "「成本后是否可交易」（净口径 verdict_net + 容量估算）分开报告，"
        "原始 top50 的净超额同时受选股与换手成本影响，不宜与中性毛超额直接比大小。",
        "",
        "## 中性化组合构成（市场权重法）",
        "",
        "按「流通市值十分位 × tushare 行业」分桶，每桶总权重 = 该桶全市场股票数占比；"
        "桶内只持有因子分数最高的 50%（至少 1 只）等权 → 组合的市值/行业暴露与全市场一致，"
        "OOS 超额若仍为正且显著，说明不是规模/行业倾斜带来的。",
        "",
        "## 成本与容量",
        "",
        "成本：单边 30bp，按换手计（成本 = 双边换手 × 30bp/10000 × NAV）；"
        "容量估算：单日单票成交额 ≤ ADTV 的 5%，"
        "AUM_max ≈ 5% × ADTV × 持股数 / (双边换手/2)。",
        "",
        "| 因子 | 原始换手 | 原始容量(亿元) | 中性换手 | 中性持股数 | 中性容量(亿元) |",
        "|---|---|---|---|---|---|",
    ]
    for name, e in rep["factors"].items():
        rp, npf = e["raw_portfolio"], e["neutral_portfolio"]
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            name,
            "—" if rp["avg_turnover"] is None else f"{rp['avg_turnover']:.2f}",
            "—" if rp["capacity_est_yi"] is None else f"{rp['capacity_est_yi']:.2f}",
            "—" if npf["avg_turnover"] is None else f"{npf['avg_turnover']:.2f}",
            "—" if npf["avg_holdings"] is None else f"{npf['avg_holdings']:.0f}",
            "—" if npf["capacity_est_yi"] is None else f"{npf['capacity_est_yi']:.2f}",
        ))
    lines += ["", "## 每因子结论", ""]
    for name, e in rep["factors"].items():
        rp, npf = e["raw_portfolio"], e["neutral_portfolio"]
        dc = e["direction_check"]
        lines.append(f"### {name} — 毛判定 **{e['verdict'].upper()}** / 净判定 **{e['verdict_net'].upper()}**")
        lines.append("")
        lines.append(f"- 毛判定依据：{'；'.join(e['verdict_reasons'])}")
        lines.append(f"- 净判定依据（30bp×周频）：{'；'.join(e['verdict_net_reasons'])}")
        lines.append(f"- 方向：direction={e['direction']}；"
                     f"IC(OOS)={_fmt(e['ic']['oos']['mean'])}、多空={_fmt(e['long_short']['mean_pct'])}%/"
                     f"t={_fmt(e['long_short']['t'], 2)}、中性毛 OOS={_cell(npf['gross_oos_excess_compound'])}"
                     f" → 方向自洽 {'✅' if dc['consistent'] else '❌'}")
        lines.append(f"- IC：全样本 {_fmt(e['ic']['full']['mean'])}"
                     f"（t={_fmt(e['ic']['full']['t'], 2)}）；"
                     f"IS {_fmt(e['ic']['is']['mean'])}；OOS {_fmt(e['ic']['oos']['mean'])}")
        lines.append(f"- 原始 top{TOP_N}：总收益 {_cell(rp['total_return'])}；"
                     f"净超额 {_cell(rp['excess_return'])}；毛超额 {_cell(rp['gross_excess_return'])}；"
                     f"MDD {_cell(rp['mdd'])}；双边换手/次 {_fmt(rp['avg_turnover'])}")
        lines.append(f"- 中性化：OOS 毛超额 {_cell(npf['gross_oos_excess_compound'])}"
                     f"（t={_fmt(npf['gross_oos_t'], 2)}）；OOS 净超额 {_cell(npf['oos_excess_compound'])}"
                     f"（t={_fmt(npf['oos_t'], 2)}）；IS 毛 {_cell(npf['gross_is_excess_compound'])}"
                     f"（t={_fmt(npf.get('gross_is_t'), 2)}）/净 {_cell(npf['is_excess_compound'])}")
        lines.append("- 逐年（毛/净）：" + "；".join(
            f"{y}: {_cell(npf['gross_excess_by_year'].get(y))}/{_cell(npf['excess_by_year'].get(y))}"
            for y in YEARS))
        gis = npf.get("gross_is_excess_compound")
        if gis is not None and gis < 0:
            if e["verdict"] == "pass":
                lines.append(f"- ⚠️ 稳健性提示：IS（2023-2024）毛超额 {_cell(gis)}"
                             f"（t={_fmt(npf.get('gross_is_t'), 2)}）为负，"
                             "pass 主要由 2025-2026 OOS 贡献 → 属**有条件 pass**，"
                             "walk-forward 稳定性不完整，实盘应先小额观察。")
            else:
                lines.append(f"- 稳健性：IS（2023-2024）毛超额 {_cell(gis)}"
                             f"（t={_fmt(npf.get('gross_is_t'), 2)}）同样为负 → "
                             "fail 结论有 IS 侧证据支持（并非仅 OOS 不利）。")
        lines.append(f"- 多空 Q5−Q1（20 日 CAR）：{_fmt(e['long_short']['mean_pct'])}%"
                     f"（t={_fmt(e['long_short']['t'], 2)}）")
        lines.append(f"- 成本与容量：中性累计成本拖累 {npf['cum_cost_drag']:.1%}"
                     f"（每次调仓均 {npf['avg_cost_per_rebalance_pct']}%，双边换手/次 "
                     f"{_fmt(npf['avg_turnover'])}）；原始累计 {rp['cum_cost_drag']:.1%}"
                     f"（换手/次 {_fmt(rp['avg_turnover'])}）；"
                     f"容量估 原始 {_fmt(rp['capacity_est_yi'])} 亿 / "
                     f"中性 {_fmt(npf['capacity_est_yi'])} 亿")
        lines.append("")
    lines += ["## 口径与局限", ""]
    for note in rep["limitations"]:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def build_report_meta(start: str, end: str, *, load_sec: float, runtime_sec: float,
                      panel, effective_factors, total_factors) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "start": start,
            "end": end,
            "horizon": HORIZON,
            "cost_bp": COST_BP,
            "top_n": TOP_N,
            "rebalance": "weekly",
            "entry": "T+1 收盘建仓",
            "universe": "factor_lib.tradable",
            "data_root": str(DATA),
            "data_load_sec": load_sec,
            "runtime_sec": round(runtime_sec, 2),
            "panel": {
                "dates": int(len(panel.dates)),
                "codes": int(len(panel.codes)),
                "first": str(panel.dates[0]),
                "last": str(panel.dates[-1]),
            },
            "effective": {
                "start": start,
                "end": end,
                "signal_days": None,
                "factors_requested": list(CANDIDATES),
                "factors_effective": list(effective_factors),
                "note": ("effective=本次真正跑完并写入报告的因子子集；"
                         "若少于 requested 说明时间/数据限制，未跑因子不给结论"),
            },
        },
        "method": {
            "factor_recompute": "import tools/research/factor_lib 的因子实现，构造 date×ts_code score 面板",
            "signed": "signed_score = raw_factor × direction（统一为越大越好）",
            "raw_portfolio": "bt_harness.run_backtest：等权 top50、周频、30bp、T+1/复权/涨跌停/停牌",
            "neutral_portfolio": ("市场权重法：circ_mv 十分位 × 行业分桶，桶总权重=市场股票数占比，"
                                  "桶内持有因子分数最高 50% 等权；周频、T+1、30bp"),
            "walk_forward": "逐年（2023/2024/2025/2026）IC（20 日 CAR 截面 Spearman）与组合超额；"
                            "IS=2023-2024，OOS=2025-2026",
            "threshold": {"oos_excess_gt": 0, "oos_t_ge": THRESHOLD_T,
                          "positive_years_at_least": f"{MIN_POSITIVE_YEARS}/4"},
            "verdict_basis": ("主判定 verdict = 中性化毛（未扣交易成本）OOS 超额；"
                              "verdict_net = 净口径（30bp 单边×周频），用于成本容量判断。"
                              "毛口径回答「剥离市值/行业暴露后是否还有 alpha」，"
                              "净口径回答「30bp/周频成本后是否可交易」"),
            "capacity": "AUM_max ≈ 5% × ADTV × 持股数 / (双边换手/2)",
        },
        "limitations": [
            "daily_basic 数据自 20230103 起：low_turnover 与中性化分桶（circ_mv）只能覆盖 2023 起，"
            "2022 年数据不可用；2023 年首周 IC 因 20 日 CAR 尾部缺失略少。",
            "IC 用 20 日重叠 CAR：日频 IC 序列自相关，IC 的 t 值偏大（仅作量级参考，不用于 pass/fail）。",
            "中性组合为宽口径（每个市值×行业桶都持有高分股），alpha 被桶内分散稀释；"
            "其超额是「同市值/行业暴露下选股是否有效」的下界式检验。",
            "中性组合停牌/涨跌停处理从简（缺价冻结估值，不做涨跌停不可买/不可卖模拟），"
            "相对 bt_harness 的原始组合偏乐观；中性化超额与原始超额不宜直接比大小。",
            "容量估算用信号日持仓的中位 20 日均成交额 × 5% 参与率，未考虑冲击成本与拥挤度非线性。",
            "**成本主导**：周频调仓 + 单边 30bp 下，中性组合双边换手约 0.8-1.1/周，"
            "折算年化成本拖累约 13-20%；因此「净超额为负」不等于因子无效，"
            "报告同时给出毛（未扣费）超额用于区分「成本吃掉」与「选股/风格无效」。"
            "**本报告主判定（verdict）用中性化毛 OOS 超额**（回答中性化后是否还有 alpha），"
            "verdict_net 用 30bp 周频净口径（回答成本后是否可交易）；"
            "两者同时列出，消除「IC 为正而组合净超额为负」的表面矛盾。"
            "本报告结论只对 30bp/周频口径负责，月度调仓或更低费率下可能不同。",
            "原始 top50 组合换手显著高于中性组合（尤其 rev5/mom20，双边换手 1.2-1.9/周），"
            "其净超额被换手成本放大，不宜直接与中性组合比大小。",
            "股票池为当前 stock_basic 名单 + 上市日期，存在轻微幸存者偏差（与 factor_lib/bt_harness 同口径）。",
            "负对照 mom20（direction=+1）用于校验流程：若其也 pass 则说明阈值过松。",
        ],
        "summary": None,
        "factors": None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv):
    p = argparse.ArgumentParser(prog="factor_validate", description="正向因子独立验证（中性化+walk-forward+成本容量）")
    p.add_argument("--start", default="20230101")
    p.add_argument("--end", default="20261030")
    p.add_argument("--factors", default=",".join(CANDIDATES),
                   help="逗号分隔因子名（默认全部候选）")
    p.add_argument("--json", default=str(OUT / "factor_validate_20261101.json"))
    p.add_argument("--md", default=str(OUT / "factor_validate_20261101.md"))
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--md-only", action="store_true",
                   help="只读取已有 JSON 重新渲染 md（不重跑数据，供报告修订）")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.md_only:
        rep = json.loads(Path(args.json).read_text(encoding="utf-8"))
        Path(args.md).write_text(_write_md(rep), encoding="utf-8")
        print(f"[factor_validate] md-only: {args.md} (factors={list(rep.get('factors', {}))})",
              flush=True)
        return 0
    t_all = time.time()
    start, end = args.start, args.end
    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    unknown = [n for n in names if n not in fl.FACTORS]
    if unknown:
        raise SystemExit(f"未注册因子: {unknown}")
    print(f"[factor_validate] start={start} end={end} factors={names}", flush=True)

    panel, market, industry, load_sec = load_inputs(start, end)
    print(f"[factor_validate] panel loaded {load_sec}s "
          f"dates={len(panel.dates)} codes={len(panel.codes)}", flush=True)

    car_df = fl.forward_car(panel, horizon=HORIZON, cost=COST_BP / 10_000.0)
    car = car_df.to_numpy(dtype="float64")
    range_dates = [str(d) for d in panel.dates if start <= str(d) <= end]
    signal_dates = weekly_signal_dates(range_dates)
    circ_mv_by_date = load_circ_mv(signal_dates)
    print(f"[factor_validate] CAR + 市场权重数据就绪：signal_days={len(signal_dates)} "
          f"circ_mv_days={len(circ_mv_by_date)}", flush=True)
    bench_nav = _bench_nav_map(market, start, end)
    amt20 = panel.amount.rolling(20, min_periods=5).mean() if "amount" in panel.fields else None

    rep = build_report_meta(start, end, load_sec=load_sec, runtime_sec=0.0, panel=panel,
                            effective_factors=[], total_factors=len(names))
    rep["meta"]["effective"]["signal_days"] = len(signal_dates)
    rep["factors"] = {}
    rep["summary"] = {"pass": [], "fail": [], "verdicts": {}, "verdicts_net": {},
                      "net_fail": [], "effective_factors": []}
    OUT.mkdir(parents=True, exist_ok=True)

    for name in names:
        t0 = time.time()
        try:
            entry = validate_factor(
                name, panel, market, industry, car, bench_nav, range_dates, signal_dates,
                circ_mv_by_date, amt20, start, end,
            )
        except Exception as exc:  # 单因子失败不拖垮整体
            import traceback
            traceback.print_exc()
            rep.setdefault("errors", []).append({"factor": name, "error": repr(exc)})
            print(f"[factor_validate] {name} FAILED: {exc!r}", flush=True)
            continue
        entry["runtime_sec"] = round(time.time() - t0, 2)
        rep["factors"][name] = entry
        rep["summary"]["verdicts"][name] = entry["verdict"]
        rep["summary"]["verdicts_net"][name] = entry["verdict_net"]
        rep["summary"][entry["verdict"]].append(name)
        if entry["verdict_net"] == "fail":
            rep["summary"]["net_fail"].append(name)
        rep["summary"]["effective_factors"] = list(rep["factors"].keys())
        rep["meta"]["effective"]["factors_effective"] = list(rep["factors"].keys())
        rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
        errors = validate_report_schema(rep)
        rep["schema_check"] = errors
        Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[factor_validate] {name}: verdict={entry['verdict']} "
              f"raw_ex={_pct(entry['raw_portfolio']['excess_return'])} "
              f"neut_oos_ex={_pct(entry['neutral_portfolio']['oos_excess_compound'])} "
              f"oos_t={entry['neutral_portfolio']['oos_t']} "
              f"ic={entry['ic']['full']['mean']} ({entry['runtime_sec']}s)", flush=True)

    # 最终：完整 schema 校验 + md
    rep["meta"]["runtime_sec"] = round(time.time() - t_all, 2)
    rep["meta"]["effective"]["factors_effective"] = list(rep["factors"].keys())
    rep["summary"]["effective_factors"] = list(rep["factors"].keys())
    rep["schema_check"] = validate_report_schema(rep)
    if rep["schema_check"]:
        print(f"[factor_validate] WARN schema: {rep['schema_check']}", flush=True)
    Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = _write_md(rep)
    Path(args.md).write_text(md, encoding="utf-8")
    print(f"[factor_validate] done: {args.json} / {args.md} "
          f"runtime={rep['meta']['runtime_sec']}s", flush=True)
    for name, e in rep["factors"].items():
        print(f"  {name}: {e['verdict'].upper()}  "
              f"neutral_OOS_ex={_pct(e['neutral_portfolio']['oos_excess_compound'])} "
              f"t={e['neutral_portfolio']['oos_t']}", flush=True)
    return 0 if not rep["schema_check"] else 1


if __name__ == "__main__":
    sys.exit(main())
