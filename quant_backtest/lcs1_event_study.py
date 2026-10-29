#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LCS-1 A6-A 事件研究（pilot，L1-only）。

候选池信号（accumulate/runup）→ t+1 开盘入场 → 前向 5/10/20 日
个股收益 - 同池等权基准收益。只读 tdx 冻结数据，不写数据根。

用法：
  python -X utf8 quant_backtest/lcs1_event_study.py --window in --max-codes 120
  python -X utf8 quant_backtest/lcs1_event_study.py --window out --max-codes 120
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import chart_hypothesis as CH  # noqa: E402

DEFAULT_ROOT = REPO / "quant_backtest/data/tdx"
WINDOWS = {"in": ("20230731", "20250630"), "out": ("20250701", "20261016")}
CANDIDATES = {"accumulate", "runup"}
HORIZONS = (5, 10, 20)


def load_universe(root: Path, start: str, end: str, max_codes: int) -> pd.DataFrame:
    stock = pd.read_parquet(root / "ts_stock_basic.parquet")
    codes = sorted(set(str(x) for x in stock["ts_code"].dropna()))[:max_codes]
    d = ds.dataset(str(root / "ts_daily"), format="parquet")
    t = d.to_table(filter=ds.field("ts_code").isin(codes),
                   columns=["ts_code", "trade_date", "open", "high", "low", "close",
                            "pre_close", "pct_chg", "vol", "amount"])
    df = t.to_pandas()
    a = ds.dataset(str(root / "ts_adj_factor"), format="parquet")
    at = a.to_table(filter=ds.field("ts_code").isin(codes),
                    columns=["ts_code", "trade_date", "adj_factor"]).to_pandas()
    df = df.merge(at, on=["ts_code", "trade_date"], how="left")
    df["adj_factor"] = df.groupby("ts_code")["adj_factor"].ffill().fillna(1.0)
    for c in ("open", "high", "low", "close", "pre_close"):
        df[c] = df[c] * df["adj_factor"]
    df = df.rename(columns={"vol": "volume"})
    df = df[(df.trade_date.astype(str) >= start) & (df.trade_date.astype(str) <= end)]
    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _agg(records: list[dict]) -> dict:
    out = {}
    for key in CANDIDATES:
        out[key] = {}
        for h in HORIZONS:
            xs = [r["excess"] for r in records if r["primary"] == key and r["h"] == h]
            if not xs:
                out[key][str(h)] = {"n": 0, "mean_excess": None, "win_rate": None}
                continue
            out[key][str(h)] = {
                "n": len(xs),
                "mean_excess": round(sum(xs) / len(xs), 6),
                "win_rate": round(sum(1 for x in xs if x > 0) / len(xs), 4),
            }
    return out


def run(root: Path, window: str, max_codes: int, step: int = 5) -> dict:
    start, end = WINDOWS[window]
    df = load_universe(root, start, end, max_codes)
    if df.empty:
        return {"ok": False, "error": "empty_universe"}
    po = df.pivot(index="trade_date", columns="ts_code", values="open")
    pc = df.pivot(index="trade_date", columns="ts_code", values="close")
    records = []
    for code, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 300:
            continue
        for i in range(250, len(g) - max(HORIZONS) - 1, step):
            try:
                ops = CH.operators(g.iloc[: i + 1].tail(300))
                primary = CH.primary_hypothesis(ops).get("primary")
            except Exception:
                continue
            if primary not in CANDIDATES:
                continue
            tdate = g["trade_date"].iloc[i]
            entry = g["trade_date"].iloc[i + 1]
            if entry not in po.index:
                continue
            for h in HORIZONS:
                exit_i = i + 1 + h
                if exit_i >= len(g):
                    continue
                xdate = g["trade_date"].iloc[exit_i]
                if xdate not in pc.index:
                    continue
                stock = g["close"].iloc[exit_i] / g["open"].iloc[i + 1] - 1.0
                bench_row = (pc.loc[xdate] / po.loc[entry] - 1.0).dropna()
                if len(bench_row) < 10:
                    continue
                records.append({"code": code, "date": str(tdate), "primary": primary,
                                "h": h, "stock": float(stock),
                                "bench": float(bench_row.mean()),
                                "excess": float(stock - bench_row.mean())})
    return {"ok": True, "window": window, "start": start, "end": end,
            "max_codes": max_codes, "codes": int(df.ts_code.nunique()),
            "signals": len({(r["code"], r["date"]) for r in records}),
            "records": len(records), "agg": _agg(records)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LCS-1 A6-A 事件研究 pilot")
    ap.add_argument("--data-root", default=str(DEFAULT_ROOT))
    ap.add_argument("--window", choices=tuple(WINDOWS), default="in")
    ap.add_argument("--max-codes", type=int, default=120)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    out = run(Path(args.data_root), args.window, args.max_codes, args.step)
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
