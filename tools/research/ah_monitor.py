# -*- coding: utf-8 -*-
"""A/H 溢价每日监控信号（H15 研究的可落地形态）。

读取最近 ~120 个交易日 A/H 配对 + 汇率，计算溢价 60 日 z-score，
输出「高溢价（A 相对贵）/低溢价」清单与分位，供每日盯盘与例会使用。
不自动交易；仅信号。

用法：python tools/research/ah_monitor.py [--out-dir work/research]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
WINDOW = 60
RECENT_FILES = 130


def _norm(name: str) -> str:
    s = str(name or "").strip().upper()
    for a, b in (("Ａ", "A"), (" ", ""), ("\u3000", ""), ("-", ""), ("－", ""), ("*", "")):
        s = s.replace(a, b)
    return s


def load_pairs() -> pd.DataFrame:
    hk = pq.read_table(glob.glob(str(DATA / "hk_basic" / "*.parquet"))[0]).to_pandas()
    a = pq.read_table(glob.glob(str(DATA / "stock_basic" / "*.parquet"))[0]).to_pandas()
    hk["k"] = hk["name"].map(_norm)
    a["k"] = a["name"].map(_norm)
    m = a.merge(hk, on="k", suffixes=("_a", "_h"))
    out = m[["ts_code_a", "ts_code_h", "name_a"]].copy()
    out.columns = ["a_code", "h_code", "name"]
    return out


def load_recent(prefix: str, codes: set[str]) -> pd.DataFrame:
    fs = sorted(glob.glob(str(DATA / prefix / "d_*.parquet")))[-RECENT_FILES:]
    frames = []
    for f in fs:
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close"])
        mask = pc.is_in(tb["ts_code"], value_set=__import__("pyarrow").array(list(codes)))
        tb = tb.filter(mask)
        if tb.num_rows:
            frames.append(tb.to_pandas())
    if not frames:
        return pd.DataFrame(columns=["ts_code", "trade_date", "close"])
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(REPO / "work" / "research"))
    args = ap.parse_args(argv)
    pairs = load_pairs()
    cn = pq.read_table(DATA / "fx_daily" / "USDCNH_FXCM.parquet").to_pandas()
    hkfx = pq.read_table(DATA / "fx_daily" / "USDHKD_FXCM.parquet").to_pandas()
    cn["trade_date"] = cn["trade_date"].astype(str)
    hkfx["trade_date"] = hkfx["trade_date"].astype(str)
    fx = cn.merge(hkfx, on="trade_date", suffixes=("_cn", "_hk"))
    fx["hkd_cny"] = fx["close_cn"] / fx["close_hk"]
    fx = fx[["trade_date", "hkd_cny"]]

    a = load_recent("daily", set(pairs["a_code"])).rename(columns={"ts_code": "a_code", "close": "a_close"})
    h = load_recent("hk_daily", set(pairs["h_code"])).rename(columns={"ts_code": "h_code", "close": "h_close"})
    df = a.merge(h, on="trade_date").merge(fx, on="trade_date").merge(pairs, on=["a_code", "h_code"])
    df["h_cny"] = df["h_close"] * df["hkd_cny"]
    df["premium"] = df["a_close"] / df["h_cny"] - 1
    df = df.sort_values(["name", "trade_date"])
    g = df.groupby("name")
    df["a_ret"] = g["a_close"].pct_change()
    df["h_ret_cny"] = g["h_cny"].pct_change()
    corr = df.groupby("name")[["a_ret", "h_ret_cny"]].apply(
        lambda x: x["a_ret"].corr(x["h_ret_cny"]) if len(x) > 30 else np.nan)
    good = set(corr[corr >= 0.3].index)
    df = df[df["name"].isin(good)]
    prem = df.pivot_table(index="trade_date", columns="name", values="premium")
    mean = prem.rolling(WINDOW, min_periods=40).mean()
    std = prem.rolling(WINDOW, min_periods=40).std()
    z = (prem - mean) / std
    last = z.dropna(how="all").iloc[-1]
    latest_date = str(last.name)
    lvl = prem.iloc[-1]
    out = []
    for name in last.index:
        if pd.isna(last[name]) or pd.isna(lvl.get(name)):
            continue
        out.append({"name": name, "premium": round(float(lvl[name]) * 100, 2),
                    "z": round(float(last[name]), 2)})
    out.sort(key=lambda x: x["z"], reverse=True)
    high = out[:12]
    low = out[-12:][::-1]
    sig = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "date": latest_date, "pairs": len(out), "window": WINDOW,
           "high_premium_z": high, "low_premium_z": low,
           "median_premium_pct": round(float(lvl.median()) * 100, 2) if len(lvl) else None,
           "note": "高 z=溢价处 60 日高位（A 相对贵，收敛方向：空 A/多 H）；低 z 反之。仅信号不做交易。"}
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    js = outdir / ("ah_signal_%s.json" % latest_date.replace("-", ""))
    js.write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
    md = outdir / ("ah_signal_%s.md" % latest_date.replace("-", ""))
    lines = ["# A/H 溢价监控信号 · %s" % latest_date, "",
             "配对 %d 对；60 日 z；中位溢价 %.2f%%" % (sig["pairs"], sig["median_premium_pct"] or 0), "",
             "## 高溢价（A 相对贵，收敛方向 空A/多H）", "| 名称 | 溢价 | z |", "|---|---|---|"]
    lines += ["| %s | %.2f%% | %.2f |" % (x["name"], x["premium"], x["z"]) for x in high]
    lines += ["", "## 低溢价（A 相对便宜，收敛方向 多A/空H）", "| 名称 | 溢价 | z |", "|---|---|---|"]
    lines += ["| %s | %.2f%% | %.2f |" % (x["name"], x["premium"], x["z"]) for x in low]
    lines += ["", "> 仅研究信号，不构成交易指令；做空可行性（融券/借券）需另评估。"]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"date": latest_date, "pairs": len(out), "json": str(js), "md": str(md),
                      "top": high[:3], "bottom": low[:3]}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
