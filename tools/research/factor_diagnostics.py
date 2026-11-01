# -*- coding: utf-8 -*-
"""脱机因子诊断工具 v1（2026-11-01 WO-STRAT-FDIAG-20261101 / task-24）。

Alphalens 思路的**只读研究诊断**：输入一张已对齐的因子面板，输出因子有效性的
描述统计。纯 pandas/numpy（不新增依赖、不联网、不写生产数据、不接生产选股）。

输入 DataFrame 必需列：
    date            交易日（可被 pd.to_datetime 解析的字符串/Timestamp）
    symbol          证券代码（任意唯一标识）
    factor          因子值（数值，越大代表越看多——本工具不做方向假设）
    forward_return  前视收益（**小数**，如 0.02=2%；由调用方按 PIT 口径自行计算，
                    本工具不做价格对齐/复权/停牌处理，也不做前视校验）

输出（dict，JSON 友好）：
    coverage     覆盖：交易日数、每日截面股票数、样本行数、坏行数
    ic           日度截面 rank IC（Spearman）与 Pearson IC：
                 mean/std/ICIR(mean/std)/t≈ICIR*sqrt(n_days)/正比例/n_days
    quantiles    分位收益（1=因子最低 … n=最高，按日等权再跨日平均）、
                 分位单调性判定、多空差（top-bottom）均值/std/IR/正比例
    turnover     最高分位组合的逐期单边换手（成员替换率）、均值/中位/pairs
    warnings     数据质量告警（截面不足/重复/坏行样例等，fail-visible）

口径与边界（详见 docs/audit/2026-11-01_因子诊断工具_v1.md）：
  * 按日分组，日度截面 IC 只统计截面股票数 ≥ min_names_per_day 且因子/收益非常数的交易日；
  * quantile 用 rank(method="first") 后 qcut，保证并列值稳定分层；截面不足 n_quantiles 的日剔除；
  * ICIR = 日度 IC 均值 / 日度 IC 标准差（ddof=1），**不做年化**（与 Alphalens 的 IC 统计口径一致，
    t 值也仅作描述，不是显著性检验/多重检验修正）；
  * 换手定义：top 分位相邻两期的 `|新−旧| / |新|`（0~1，单边成员替换率；全换仓=1），
    与 bt_harness 的持仓权重换手口径不同，仅用于因子衰减/交易频率的粗诊断；
  * 全部结论仅为研究诊断，不构成投资建议；上线需过 PIT/成本/影子盘/人工批准。

CLI：
    python tools/research/factor_diagnostics.py --csv factor_panel.csv --json
    python tools/research/factor_diagnostics.py --csv factor_panel.csv --out research/reports/factor_diag_x.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "factor_diagnostics/v1"
REQUIRED_COLUMNS = ("date", "symbol", "factor", "forward_return")
DEFAULT_QUANTILES = 5
DEFAULT_MIN_NAMES = 5
DEFAULT_MIN_DAYS = 3
MAX_BAD_EXAMPLES = 20
MAX_DAILY_ROWS = 1000


class FactorDiagnosticsError(ValueError):
    """结构性失败（输入非 DataFrame/缺列/无有效行）——fail-visible，不静默返回空报告。"""


# ---------------------------------------------------------------- 清洗
def _clean_frame(df: pd.DataFrame, *, min_names_per_day: int):
    """校验/清洗输入 → (clean_df, meta)。结构性问题抛错，坏行收集为告警。"""
    if df is None or not isinstance(df, pd.DataFrame):
        raise FactorDiagnosticsError("输入必须是 pandas.DataFrame，实际 %s"
                                     % type(df).__name__)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise FactorDiagnosticsError(
            "缺列：%s（必需列 %s）" % (", ".join(missing), ", ".join(REQUIRED_COLUMNS)))
    if df.empty:
        raise FactorDiagnosticsError("空数据：输入 DataFrame 无任何行")

    work = df.copy()
    try:  # pandas>=2：逐元素解析，避免混合格式的 infer warning/误解析
        work["_date"] = pd.to_datetime(work["date"], errors="coerce",
                                       format="mixed")
    except (TypeError, ValueError):  # pandas<2 回退
        work["_date"] = pd.to_datetime(work["date"], errors="coerce")
    work["_symbol"] = work["symbol"].astype("string").str.strip()
    work["_factor"] = pd.to_numeric(work["factor"], errors="coerce")
    work["_fwd"] = pd.to_numeric(work["forward_return"], errors="coerce")
    for col in ("_factor", "_fwd"):
        work[col] = work[col].replace([np.inf, -np.inf], np.nan)

    mask_date = work["_date"].isna()
    mask_sym = work["_symbol"].isna() | work["_symbol"].eq("")
    mask_factor = work["_factor"].isna()
    mask_fwd = work["_fwd"].isna()
    bad_mask = mask_date | mask_sym | mask_factor | mask_fwd
    bad_count = int(bad_mask.sum())
    bad_examples = []
    if bad_count:
        for idx in work.index[bad_mask][:MAX_BAD_EXAMPLES]:
            reasons = []
            if bool(mask_date.loc[idx]):
                reasons.append("date 不可解析")
            if bool(mask_sym.loc[idx]):
                reasons.append("symbol 缺失/为空")
            if bool(mask_factor.loc[idx]):
                reasons.append("factor 非数值/NaN/inf")
            if bool(mask_fwd.loc[idx]):
                reasons.append("forward_return 非数值/NaN/inf")
            row_no = int(idx) if isinstance(idx, (int, np.integer)) else str(idx)
            bad_examples.append({"row": row_no, "reason": "; ".join(reasons)})
        work = work[~bad_mask].copy()
    if work.empty:
        raise FactorDiagnosticsError(
            "无有效行（输入 %d 行，坏行 %d 行）——请检查 date/factor/forward_return"
            % (len(df), bad_count))

    dup = int(len(work) - len(work.drop_duplicates(subset=["_date", "_symbol"])))
    if dup:
        work = (work.sort_values("_date")
                    .drop_duplicates(subset=["_date", "_symbol"], keep="last"))
    work = work.sort_values(["_date", "_symbol"]).reset_index(drop=True)
    work["date"] = work["_date"].dt.strftime("%Y-%m-%d")
    work = work[["date", "_symbol", "_factor", "_fwd"]].rename(
        columns={"_symbol": "symbol", "_factor": "factor", "_fwd": "forward_return"})

    cross = work.groupby("date")["symbol"].nunique()
    warnings = []
    if bad_count:
        warnings.append("坏行 %d 行已剔除（fail-visible：见 bad_rows 样例）" % bad_count)
    if dup:
        warnings.append("重复 (date,symbol) %d 行，保留最后一条" % dup)
    small = cross[cross < int(min_names_per_day)]
    if len(small):
        warnings.append("%d/%d 个交易日截面不足 %d 只（不参与 IC/分位计算，仅计入覆盖）"
                        % (len(small), len(cross), int(min_names_per_day)))
    meta = {
        "rows_in": int(len(df)),
        "rows_used": int(len(work)),
        "bad_rows": bad_count,
        "bad_rows_examples": bad_examples,
        "duplicate_rows": dup,
        "warnings": warnings,
        "coverage": {
            "dates": int(cross.size),
            "names_mean": round(float(cross.mean()), 2) if cross.size else None,
            "names_median": float(cross.median()) if cross.size else None,
            "names_min": int(cross.min()) if cross.size else None,
            "names_max": int(cross.max()) if cross.size else None,
            "days_below_min": int(len(small)),
            "min_names_per_day": int(min_names_per_day),
        },
    }
    return work, meta


# ---------------------------------------------------------------- 统计小工具
def _corr(x, y, method: str):
    """有限样本、非常数才计算；否则 None（不把退化为 0 的相关系数当信号）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return None
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return None
    if method == "spearman":
        x = pd.Series(x).rank().to_numpy()
        y = pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(x, y)[0, 1])


def _stats(values) -> dict:
    vals = np.asarray([v for v in values if v is not None], dtype=float)
    n = int(len(vals))
    if n == 0:
        return {"mean": None, "std": None, "icir": None, "t_stat": None,
                "positive_rate": None, "n_days": 0}
    mean = float(np.mean(vals))
    std = float(np.std(vals, ddof=1)) if n >= 2 else None
    icir = (mean / std) if (std is not None and std > 0) else None
    return {
        "mean": round(mean, 6),
        "std": round(std, 6) if std is not None else None,
        "icir": round(icir, 6) if icir is not None else None,
        "t_stat": round(icir * float(np.sqrt(n)), 4) if icir is not None else None,
        "positive_rate": round(float(np.mean(vals > 0)), 4),
        "n_days": n,
    }


# ---------------------------------------------------------------- 主计算
def compute(df: pd.DataFrame, *, n_quantiles: int = DEFAULT_QUANTILES,
            min_names_per_day: int = DEFAULT_MIN_NAMES,
            min_days: int = DEFAULT_MIN_DAYS) -> dict:
    """因子诊断主入口 → 报告 dict（JSON 友好）。

    结构性错误（缺列/空数据/全部坏行）抛 FactorDiagnosticsError；
    数据质量不足（截面过小/天数不足）不抛，报告里 None + warnings（fail-visible）。
    """
    n_quantiles = int(n_quantiles)
    min_names_per_day = int(min_names_per_day)
    min_days = int(min_days)
    if not 2 <= n_quantiles <= 20:
        raise FactorDiagnosticsError("n_quantiles 必须在 2..20（收到 %d）" % n_quantiles)
    if min_names_per_day < 2:
        raise FactorDiagnosticsError("min_names_per_day 必须 ≥2")
    if min_days < 1:
        raise FactorDiagnosticsError("min_days 必须 ≥1")

    clean, meta = _clean_frame(df, min_names_per_day=min_names_per_day)
    warnings = list(meta["warnings"])

    daily = []
    quant_returns: dict[int, list[float]] = {q: [] for q in range(1, n_quantiles + 1)}
    quant_counts: dict[int, int] = {q: 0 for q in range(1, n_quantiles + 1)}
    long_short = []
    top_sets: list[tuple[str, set]] = []
    skipped_small = skipped_quantile = 0
    for date, group in clean.groupby("date", sort=True):
        n = int(len(group))
        if n < min_names_per_day:
            skipped_small += 1
            continue
        factor = group["factor"].to_numpy(dtype=float)
        fwd = group["forward_return"].to_numpy(dtype=float)
        rank_ic = _corr(factor, fwd, "spearman")
        pearson_ic = _corr(factor, fwd, "pearson")
        labels = None
        try:
            ranks = group["factor"].rank(method="first")
            labels = pd.qcut(ranks, n_quantiles, labels=False, duplicates="drop")
            if labels is not None and int(labels.nunique()) == n_quantiles:
                labels = labels.astype(int) + 1      # 1=因子最低 … n=最高
            else:
                labels = None
        except (ValueError, IndexError):
            labels = None
        if labels is None:
            skipped_quantile += 1
        else:
            grouped = group.assign(_q=labels.to_numpy())
            q_means = grouped.groupby("_q")["forward_return"].mean()
            for q in range(1, n_quantiles + 1):
                if q in q_means.index:
                    quant_returns[q].append(float(q_means.loc[q]))
                    quant_counts[q] += int((grouped["_q"] == q).sum())
            top_set = set(grouped.loc[grouped["_q"] == n_quantiles, "symbol"].tolist())
            bottom_set = set(grouped.loc[grouped["_q"] == 1, "symbol"].tolist())
            if top_set and bottom_set:
                top_sets.append((date, top_set))
                long_short.append({
                    "date": date,
                    "n": n,
                    "top_mean": round(float(
                        grouped.loc[grouped["_q"] == n_quantiles,
                                    "forward_return"].mean()), 6),
                    "bottom_mean": round(float(
                        grouped.loc[grouped["_q"] == 1,
                                    "forward_return"].mean()), 6),
                })
        daily.append({
            "date": date,
            "n": n,
            "rank_ic": None if rank_ic is None else round(rank_ic, 6),
            "pearson_ic": None if pearson_ic is None else round(pearson_ic, 6),
        })

    ic_days = [d for d in daily if d["rank_ic"] is not None]
    rank_stats = _stats([d["rank_ic"] for d in daily])
    pearson_stats = _stats([d["pearson_ic"] for d in daily])

    q_means = {}
    for q in range(1, n_quantiles + 1):
        vals = quant_returns[q]
        q_means[str(q)] = round(float(np.mean(vals)), 6) if vals else None
    q_series = [q_means[str(q)] for q in range(1, n_quantiles + 1)]
    quantile_spearman = _corr(list(range(1, n_quantiles + 1)),
                              [v if v is not None else np.nan for v in q_series],
                              "spearman") if all(v is not None for v in q_series) else None
    diffs = [q_series[i + 1] - q_series[i] for i in range(len(q_series) - 1)] \
        if all(v is not None for v in q_series) else []
    monotonic_up = bool(diffs) and all(d > 0 for d in diffs)
    monotonic_down = bool(diffs) and all(d < 0 for d in diffs)

    ls_vals = [round(t["top_mean"] - t["bottom_mean"], 6) for t in long_short]
    ls_stats = _stats(ls_vals)

    turnover_vals = []
    for (prev_date, prev_set), (date, new_set) in zip(top_sets, top_sets[1:]):
        if not new_set:
            continue
        turnover_vals.append(len(new_set - prev_set) / float(len(new_set)))
    turnover = {
        "mean": round(float(np.mean(turnover_vals)), 4) if turnover_vals else None,
        "median": round(float(np.median(turnover_vals)), 4) if turnover_vals else None,
        "n_pairs": len(turnover_vals),
        "definition": "top 分位相邻两期 |新−旧|/|新|（单边成员替换率 0~1）",
        "top_quantile": n_quantiles,
    }

    if rank_stats["n_days"] and rank_stats["n_days"] < int(min_days):
        warnings.append("有效 IC 交易日仅 %d < min_days=%d——统计量不稳定（仅描述，不作结论）"
                        % (rank_stats["n_days"], int(min_days)))
    if skipped_quantile:
        warnings.append("%d 个交易日无法分 %d 位（截面/并列值不足），已跳过分位统计"
                        % (skipped_quantile, n_quantiles))
    if skipped_small:
        warnings.append("%d 个交易日截面不足 %d 只，已跳过 IC/分位"
                        % (skipped_small, min_names_per_day))
    if not ic_days:
        warnings.append("没有可计算 IC 的交易日（检查截面大小/因子是否常数）")

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "params": {"n_quantiles": n_quantiles,
                   "min_names_per_day": min_names_per_day,
                   "min_days": int(min_days)},
        "coverage": meta["coverage"],
        "samples": {
            "rows_in": meta["rows_in"],
            "rows_used": meta["rows_used"],
            "bad_rows": meta["bad_rows"],
            "duplicate_rows": meta["duplicate_rows"],
            "days_total": meta["coverage"]["dates"],
            "days_ic": rank_stats["n_days"],
            "days_quantile": len(long_short),
            "days_skipped_small": skipped_small,
            "days_skipped_quantile": skipped_quantile,
        },
        "ic": {
            "rank": {**rank_stats, "method": "日度截面 Spearman(factor, forward_return)"},
            "pearson": {**pearson_stats, "method": "日度截面 Pearson(factor, forward_return)"},
            "definition": "ICIR=日度 IC 均值/标准差(ddof=1)（不年化）；t=ICIR*sqrt(n_days) 仅描述",
            "daily": daily[:MAX_DAILY_ROWS],
            "daily_total": len(daily),
        },
        "quantiles": {
            "n_quantiles": n_quantiles,
            "returns": q_means,
            "counts": {str(q): quant_counts[q] for q in range(1, n_quantiles + 1)},
            "monotonic_up": monotonic_up,
            "monotonic_down": monotonic_down,
            "quantile_spearman": round(quantile_spearman, 6)
            if quantile_spearman is not None else None,
            "long_short": {
                **ls_stats,
                "definition": "每日 top 分位均值收益 − bottom 分位均值收益；跨日平均/IR",
                "daily": long_short[:MAX_DAILY_ROWS],
            },
        },
        "turnover": turnover,
        "bad_rows": meta["bad_rows_examples"],
        "warnings": warnings,
        "notes": [
            "研究诊断只读：不构成投资建议，不接生产选股/策略注册。",
            "forward_return 由调用方按 PIT 口径自备；本工具不做价格对齐/复权/停牌/前视校验。",
            "ICIR 不年化；t 值仅描述，不构成显著性/多重检验结论。",
        ],
    }


# 语义别名
compute_factor_diagnostics = compute


# ---------------------------------------------------------------- CLI
def _print_report(report: dict) -> None:
    ic = report["ic"]["rank"]
    q = report["quantiles"]
    ls = q["long_short"]
    print("== 因子诊断 %s ==" % report["schema"])
    print("样本: used=%s/%s bad=%s days=%s (IC days=%s)"
          % (report["samples"]["rows_used"], report["samples"]["rows_in"],
             report["samples"]["bad_rows"], report["samples"]["days_total"],
             report["samples"]["days_ic"]))
    print("覆盖: 每日截面 mean=%s median=%s min=%s max=%s below_min=%s"
          % (report["coverage"]["names_mean"], report["coverage"]["names_median"],
             report["coverage"]["names_min"], report["coverage"]["names_max"],
             report["coverage"]["days_below_min"]))
    print("RankIC: mean=%s std=%s ICIR=%s t=%s 正比例=%s n_days=%s"
          % (ic["mean"], ic["std"], ic["icir"], ic["t_stat"],
             ic["positive_rate"], ic["n_days"]))
    print("分位收益(%d位, 1=最低): %s"
          % (q["n_quantiles"],
             ", ".join("Q%s=%s" % (k, v) for k, v in q["returns"].items())))
    print("单调性: up=%s down=%s quantile_spearman=%s"
          % (q["monotonic_up"], q["monotonic_down"], q["quantile_spearman"]))
    print("多空差: mean=%s std=%s IR=%s 正比例=%s n_days=%s"
          % (ls["mean"], ls["std"], ls["icir"], ls["positive_rate"], ls["n_days"]))
    print("换手: mean=%s median=%s pairs=%s"
          % (report["turnover"]["mean"], report["turnover"]["median"],
             report["turnover"]["n_pairs"]))
    for w in report["warnings"]:
        print("[warn] %s" % w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="脱机因子诊断（Alphalens 思路，纯 pandas/numpy，不联网）")
    ap.add_argument("--csv", required=True,
                    help="输入 CSV（列：date,symbol,factor,forward_return）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--out", default="", help="报告 JSON 落盘路径")
    ap.add_argument("--quantiles", type=int, default=DEFAULT_QUANTILES)
    ap.add_argument("--min-names", type=int, default=DEFAULT_MIN_NAMES)
    ap.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS)
    args = ap.parse_args(argv)
    try:
        df = pd.read_csv(args.csv)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print("[ERROR] 读取 CSV 失败：%s" % str(exc)[:200], file=sys.stderr)
        return 2
    try:
        report = compute(df, n_quantiles=args.quantiles,
                         min_names_per_day=args.min_names,
                         min_days=args.min_days)
    except FactorDiagnosticsError as exc:
        print("[ERROR] 因子诊断失败：%s" % exc, file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            print("[report] -> %s" % out)
        except OSError as exc:
            print("[ERROR] 报告落盘失败：%s" % str(exc)[:200], file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
