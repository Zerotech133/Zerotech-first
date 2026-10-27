# -*- coding: utf-8 -*-
"""LCS-1 基线因子对照 MVP（P0⑤ · WO-LCS1-BASELINE-20261027）。

目的：给 LCS-1 候选池一个**可复现的基础因子基线**（Alpha158 子集同义实现），
计算 IC/RankIC/分组收益，供 BACK1 在同一窗口/同一口径下与 LCS-1 对比。

设计纪律：
  · 纯 pandas/numpy，无外部服务；只读行情（tdx parquet）+ 可选 daily_basic；
  · 因子只用 t 日及以前数据（rolling/shift 向后）；前向收益只在**评估**函数里算，
    且显式命名为 fwd_ret_*，避免误用；
  · 输出落 --out（默认 work/lcs1_baseline/），不写数据根；
  · MVP 不含全量回测/组合构建；对比层由 BACK1 回测清单负责。

用法：
  python -X utf8 quant_backtest/baseline_factors.py --start 2023-09-21 --end 2026-10-16 \
      --max-codes 300 --out work/lcs1_baseline/mvp --dry-run
  python -X utf8 quant_backtest/baseline_factors.py --start 2023-09-21 --end 2026-10-16 \
      --max-codes 300 --out work/lcs1_baseline/mvp
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]  # quant_backtest/
ROOT = REPO.parent


def _find_data_root() -> Path:
    """主仓/工作树都能定位到 C:\\项目\\老田的AI助手\\quant_backtest\\data\\tdx。"""
    for anc in [ROOT] + list(ROOT.parents):
        cand = anc / "quant_backtest" / "data" / "tdx"
        if (cand / "ts_daily").is_dir():
            return cand
    return ROOT / "quant_backtest" / "data" / "tdx"


DATA_ROOT = _find_data_root()

FACTORS = [
    "MOM5", "MOM10", "MOM20", "MOM60",
    "MA_DEV20", "MA_DEV60", "VOL20", "RSV20",
    "VOL_RATIO", "RSI14", "MACD_DIF", "BOLL_POS",
    "AMT20",
]


def _read_parts(folder: Path, columns: list[str] | None = None) -> pd.DataFrame:
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise SystemExit("[baseline] 无 parquet: %s" % folder)
    parts = []
    for f in files:
        try:
            parts.append(pd.read_parquet(f, columns=columns))
        except (OSError, ValueError) as exc:
            raise SystemExit("[baseline] 读取失败 %s: %s" % (f, exc))
    return pd.concat(parts, ignore_index=True)


def load_panel(data_root: Path = DATA_ROOT, start: str = "", end: str = "",
               max_codes: int = 0) -> tuple[pd.DataFrame, dict]:
    """加载日线 + 复权因子（+可选 daily_basic 换手率），返回长表与元信息。"""
    t0 = time.time()
    daily = _read_parts(data_root / "ts_daily",
                        ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"])
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily = daily.drop_duplicates(["ts_code", "trade_date"]).sort_values(["ts_code", "trade_date"])
    if start:
        daily = daily[daily["trade_date"] >= start.replace("-", "")]
    if end:
        daily = daily[daily["trade_date"] <= end.replace("-", "")]
    codes = sorted(daily["ts_code"].unique())
    if max_codes and len(codes) > max_codes:
        codes = codes[:max_codes]
        daily = daily[daily["ts_code"].isin(codes)]
    adj = _read_parts(data_root / "ts_adj_factor", ["ts_code", "trade_date", "adj_factor"])
    adj["trade_date"] = adj["trade_date"].astype(str)
    adj = adj.drop_duplicates(["ts_code", "trade_date"])
    df = daily.merge(adj, on=["ts_code", "trade_date"], how="left")
    factor_cov = float(df["adj_factor"].notna().mean()) if len(df) else 0.0
    # 无复权因子的行剔除（MVP 不猜；全量窗口待 DATA1 补全因子历史）
    df = df[df["adj_factor"].notna()].copy()
    if df.empty:
        raise SystemExit("[baseline] 合并复权因子后无数据（检查 adj_factor 覆盖）")
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] * df["adj_factor"]
    # 可选换手率（daily_basic）；缺失时用 AMT20 金额比代理并标注
    diag = {"rows": int(len(df)), "codes": int(df["ts_code"].nunique()),
            "dates": [str(df["trade_date"].min()), str(df["trade_date"].max())],
            "adj_factor_coverage": round(factor_cov, 4),
            "turnover_source": "proxy_amount20"}
    basic_dir = data_root / "ts_daily_basic"
    if basic_dir.is_dir():
        try:
            basic = _read_parts(basic_dir, ["ts_code", "trade_date", "turnover_rate"])
            basic["trade_date"] = basic["trade_date"].astype(str)
            basic = basic.drop_duplicates(["ts_code", "trade_date"])
            df = df.merge(basic, on=["ts_code", "trade_date"], how="left")
            if df["turnover_rate"].notna().mean() >= 0.5:
                diag["turnover_source"] = "ts_daily_basic.turnover_rate"
        except SystemExit:
            pass
    diag["load_seconds"] = round(time.time() - t0, 2)
    return df, diag


def _rsi14(ret: pd.Series) -> pd.Series:
    gain = ret.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-ret.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_factors(df: pd.DataFrame, turnover_source: str = "proxy_amount20") -> pd.DataFrame:
    """输入长表（含 adj_close/adj_open/vol/amount[/turnover_rate]），返回加因子的表。"""
    df = df.sort_values(["ts_code", "trade_date"]).copy()
    g = df.groupby("ts_code", sort=False)
    c = df["adj_close"]
    for n in (5, 10, 20, 60):
        df["MOM%d" % n] = g["adj_close"].transform(lambda s, n=n: s / s.shift(n) - 1)
    df["MA_DEV20"] = g["adj_close"].transform(lambda s: s / s.rolling(20, min_periods=20).mean() - 1)
    df["MA_DEV60"] = g["adj_close"].transform(lambda s: s / s.rolling(60, min_periods=60).mean() - 1)
    ret = g["adj_close"].transform(lambda s: s.pct_change())
    df["VOL20"] = ret.groupby(df["ts_code"]).transform(lambda s: s.rolling(20, min_periods=20).std())
    mn_low = g["adj_low"].transform(lambda s: s.rolling(20, min_periods=20).min())
    mx_high = g["adj_high"].transform(lambda s: s.rolling(20, min_periods=20).max())
    df["RSV20"] = (df["adj_close"] - mn_low) / (mx_high - mn_low).replace(0, np.nan)
    df["VOL_RATIO"] = g["vol"].transform(lambda s: s / s.rolling(20, min_periods=20).mean())
    df["RSI14"] = ret.groupby(df["ts_code"]).transform(_rsi14)
    ema12 = g["adj_close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = g["adj_close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
    df["MACD_DIF"] = (ema12 - ema26) / df["adj_close"].replace(0, np.nan)
    std20 = g["adj_close"].transform(lambda s: s.rolling(20, min_periods=20).std())
    ma20 = g["adj_close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["BOLL_POS"] = (df["adj_close"] - ma20) / (2 * std20).replace(0, np.nan)
    if "turnover_rate" in df.columns and turnover_source == "ts_daily_basic.turnover_rate":
        df["AMT20"] = g["turnover_rate"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    else:
        df["AMT20"] = g["amount"].transform(lambda s: s / s.rolling(20, min_periods=20).mean())
    df = df.drop(columns=["MA_DEV5"], errors="ignore")
    return df


def add_forward_returns(df: pd.DataFrame, labels: list[int]) -> pd.DataFrame:
    """前向收益：t+1 开盘入场，持有 N 交易日（到 t+1+N 开盘）；仅评估用。"""
    out = df.sort_values(["ts_code", "trade_date"]).copy()
    g = out.groupby("ts_code", sort=False)
    entry = g["adj_open"].shift(-1)
    for n in labels:
        exit_ = g["adj_open"].shift(-(n + 1))
        out["fwd_ret_%d" % n] = exit_ / entry - 1
    return out


def cross_section_ic(df: pd.DataFrame, factor: str, label: str,
                     min_obs: int = 30) -> pd.DataFrame:
    """逐日截面 Pearson IC 与 Spearman RankIC（零方差截面跳过）。"""
    rows = []
    sub = df[["trade_date", factor, label]].dropna()
    for dt, g in sub.groupby("trade_date", sort=True):
        if len(g) < min_obs:
            continue
        if g[factor].nunique() < 2 or g[label].nunique() < 2:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            ic = g[factor].corr(g[label])
            ric = g[factor].rank().corr(g[label].rank())
        if pd.isna(ic) or pd.isna(ric):
            continue
        rows.append({"trade_date": dt, "ic": float(ic), "rank_ic": float(ric), "n": int(len(g))})
    return pd.DataFrame(rows, columns=["trade_date", "ic", "rank_ic", "n"])


def factor_summary(icdf: pd.DataFrame) -> dict:
    if icdf.empty:
        return {"n_days": 0, "ic_mean": None, "rank_ic_mean": None,
                "icir": None, "rank_icir": None, "ic_win_rate": None}
    ic = icdf["ic"]
    ric = icdf["rank_ic"]
    return {
        "n_days": int(len(icdf)),
        "ic_mean": round(float(ic.mean()), 5),
        "rank_ic_mean": round(float(ric.mean()), 5),
        "ic_std": round(float(ic.std()), 5),
        "icir": round(float(ic.mean() / ic.std()), 4) if ic.std() else None,
        "rank_icir": round(float(ric.mean() / ric.std()), 4) if ric.std() else None,
        "ic_win_rate": round(float((ic > 0).mean()), 4),
    }


def group_returns(df: pd.DataFrame, factor: str, label: str, q: int = 5,
                  min_obs: int = 50) -> dict:
    """逐日按因子分 q 组，返回各组平均前向收益与多空（Q5-Q1）。"""
    sub = df[["trade_date", factor, label]].dropna()
    per_day = []
    for dt, g in sub.groupby("trade_date", sort=True):
        if len(g) < min_obs:
            continue
        try:
            bucket = pd.qcut(g[factor].rank(method="first"), q, labels=False)
        except ValueError:
            continue
        means = g.groupby(bucket)[label].mean()
        if len(means) < q:
            continue
        per_day.append([float(means.get(i, np.nan)) for i in range(q)])
    if not per_day:
        return {"n_days": 0, "groups": [], "long_short": None}
    arr = np.array(per_day, dtype=float)
    groups = [round(float(np.nanmean(arr[:, i])), 6) for i in range(q)]
    return {"n_days": int(len(per_day)), "groups": groups,
            "long_short": round(groups[-1] - groups[0], 6)}


def run(data_root: Path, start: str, end: str, max_codes: int, out: Path,
        labels: list[int], dry_run: bool) -> dict:
    plan = {
        "data_root": str(data_root), "start": start, "end": end,
        "max_codes": max_codes, "labels": labels, "out": str(out),
        "factors": FACTORS,
        "data_ok": (data_root / "ts_daily").is_dir(),
    }
    if dry_run:
        print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False, indent=1))
        return plan
    df, diag = load_panel(data_root, start, end, max_codes)
    df = compute_factors(df, turnover_source=diag["turnover_source"])
    df = add_forward_returns(df, labels)
    out.mkdir(parents=True, exist_ok=True)
    all_summary = {"schema": "lcs1_baseline/v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "diagnostics": diag, "plan": plan, "factors": {}}
    for factor in FACTORS:
        entry = {"label": {}, "coverage": round(float(df[factor].notna().mean()), 4)}
        for n in labels:
            label = "fwd_ret_%d" % n
            icdf = cross_section_ic(df, factor, label)
            entry["label"][label] = {
                "ic": factor_summary(icdf),
                "groups": group_returns(df, factor, label),
            }
        all_summary["factors"][factor] = entry
        s5 = entry["label"]["fwd_ret_5"]["ic"]
        print("[baseline] %-10s cover=%.0f%% n_days=%s rankIC=%s"
              % (factor, entry["coverage"] * 100, s5.get("n_days"), s5.get("rank_ic_mean")))
    summary_path = out / "baseline_summary.json"
    summary_path.write_text(json.dumps(all_summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # 精简 CSV：因子 × label 的 IC/RankIC/多空
    rows = []
    for factor, entry in all_summary["factors"].items():
        for label, m in entry["label"].items():
            rows.append({"factor": factor, "label": label, "coverage": entry["coverage"],
                         "n_days": m["ic"]["n_days"], "ic_mean": m["ic"]["ic_mean"],
                         "rank_ic_mean": m["ic"]["rank_ic_mean"], "icir": m["ic"]["icir"],
                         "long_short": m["groups"]["long_short"]})
    pd.DataFrame(rows).to_csv(out / "baseline_metrics.csv", index=False, encoding="utf-8-sig")
    print(json.dumps({"ok": True, "out": str(out), "summary": str(summary_path),
                      "diagnostics": diag}, ensure_ascii=False))
    return all_summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LCS-1 基线因子（IC/RankIC/分组）MVP")
    ap.add_argument("--data-root", default=str(DATA_ROOT))
    ap.add_argument("--start", default="2023-09-21")
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--max-codes", type=int, default=300)
    ap.add_argument("--labels", default="5,10,20")
    ap.add_argument("--out", default=str(ROOT / "work" / "lcs1_baseline" / "mvp"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="仅打印 JSON（配合 --dry-run）")
    args = ap.parse_args(argv)
    labels = [int(x) for x in str(args.labels).split(",") if str(x).strip()]
    data_root = Path(args.data_root)
    if not args.dry_run and not (data_root / "ts_daily").is_dir():
        print(json.dumps({"ok": False, "error": "ts_daily missing", "data_root": str(data_root)},
                         ensure_ascii=False))
        return 2
    try:
        run(data_root, args.start, args.end, args.max_codes, Path(args.out), labels, args.dry_run)
    except SystemExit as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
