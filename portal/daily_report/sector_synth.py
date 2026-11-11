#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块日线：本地自合成，替代已断供的上游板块数据。

为什么自己合成（2026-11-12）：
  原板块维度读 `sector_daily.parquet`（pull_tdx 一次性全量快照）。直连上游实测：
  `start_date=20261023` 返回 total=0 / total_exact=true，首页首条即 20261022，
  `_fetched_at` 停在 2026-10-22 17:00 —— **上游自 10-22 收盘后没再供数**。
  换个源（东财板块 / tushare sw_daily）都要额外积分或额外接口，而本机已经有：
    `index_member_all`  5922 只票 → 31 个申万 L1 行业（out_date 全空 = 全在册）
    `daily`             8752 个交易日全量行情
  二者一 join，行业等权日线即可日更（跟着 daily 一起走），不依赖任何断供的接口。

口径（写清楚，免得跟原数据不可比）：
  行业收益 = 成分股当日 pct_chg 的**等权均值**（不是市值加权）。
  等权偏小盘，与申万官方指数（市值加权）会有系统性差异，**不要拿来做绝对水平比较**，
  只用于「行业之间谁强谁弱」「上涨行业占比」这类横截面与广度判断。
  PIT：按 in_date <= 当日 且 (out_date 空 或 out_date > 当日) 取成分。

产物：lens/sector_daily_synth.parquet（本地，已在 .gitignore 内，不入库）
  trade_date  sector_code  sector_name  n_member  ret  amt  up_ratio
  **单位**：ret 沿用 tushare pct_chg 的百分数口径（ret=1.47 表示 +1.47%）；
  up_ratio 是 0~1 的比例（0.55 表示 55% 成分股上涨）；amt 是成分股 amount 之和（千元）。
  ret 别再做 ×100 换算 —— 曾按小数口径打印，把 3.97% 显示成 396.56%。

用法：
  python sector_synth.py                 # 默认最近 250 个交易日
  python sector_synth.py --days 500
  python sector_synth.py --check         # 只校验现有产物是否跟上 clock，不重算
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TS = ROOT / "quant_backtest" / "data" / "tushare"
OUT_DIR = HERE / "lens"
OUT = OUT_DIR / "sector_daily_synth.parquet"
MEMBERS = TS / "index_member_all"
DAILY = TS / "daily"


def d8(s):
    return s.astype(str).str.replace("-", "", regex=False).str.strip().str[:8]


def load_members() -> pd.DataFrame:
    files = sorted(glob.glob(str(MEMBERS / "**" / "*.parquet"), recursive=True))
    if not files:
        raise SystemExit("index_member_all 不存在：%s" % MEMBERS)
    m = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    m["in_d"] = d8(m["in_date"])
    m["out_d"] = d8(m["out_date"])
    m.loc[m["out_date"].isna(), "out_d"] = ""
    m = m[["ts_code", "l1_code", "in_d", "out_d"]].dropna()
    m["ts_code"] = m["ts_code"].astype(str)
    m["l1_code"] = m["l1_code"].astype(str)
    return m.reset_index(drop=True)


def load_names() -> dict:
    """行业代码 → 中文名。取不到就用代码本身，不编造。"""
    files = sorted(glob.glob(str(TS / "sw_classify_l1" / "**" / "*.parquet"), recursive=True))
    if not files:
        return {}
    c = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if "index_code" not in c.columns or "industry_name" not in c.columns:
        return {}
    c = c[c["index_code"].astype(str).str.len() > 0]
    return dict(zip(c["index_code"].astype(str), c["industry_name"].astype(str)))


def build(days: int, verbose: bool = False) -> pd.DataFrame:
    mem = load_members()
    names = load_names()
    files = sorted(glob.glob(str(DAILY / "d_*.parquet")))[-days:]
    if not files:
        raise SystemExit("daily 日文件不存在：%s" % DAILY)
    print("成员 %d 行 / 行业 %d 个；行情取最近 %d 个交易日（%s ~ %s）"
          % (len(mem), mem["l1_code"].nunique(), len(files),
             os.path.basename(files[0])[2:10], os.path.basename(files[-1])[2:10]))

    out = []
    for f in files:
        day = os.path.basename(f)[2:10]
        d = pd.read_parquet(f, columns=["ts_code", "pct_chg", "amount"])
        d["ts_code"] = d["ts_code"].astype(str)
        # PIT：当天有效的成分
        live = mem[(mem["in_d"] <= day) & ((mem["out_d"] == "") | (mem["out_d"] > day))]
        j = d.merge(live[["ts_code", "l1_code"]], on="ts_code", how="inner")
        if j.empty:
            continue
        g = j.groupby("l1_code").agg(n_member=("pct_chg", "size"),
                                     ret=("pct_chg", "mean"),
                                     amt=("amount", "sum"),
                                     up_ratio=("pct_chg", lambda s: float((s > 0).mean())))
        g = g[g["n_member"] >= 8]          # 成分太少时均值噪声太大，宁缺勿滥
        g["trade_date"] = day
        g["sector_code"] = g.index
        g["sector_name"] = [names.get(c, c) for c in g.index]
        out.append(g.reset_index(drop=True))
        if verbose:
            print("  %s 覆盖 %d/%d 只，行业 %d 个" % (day, len(j), len(d), len(g)))

    if not out:
        raise SystemExit("没有任何一个交易日能合成出行业收益")
    res = pd.concat(out, ignore_index=True)
    res = res[["trade_date", "sector_code", "sector_name", "n_member", "ret", "amt", "up_ratio"]]
    return res


def check() -> int:
    """校验产物是否跟上 clock（quote_daily 最新日）。"""
    if not OUT.exists():
        print("产物不存在：%s" % OUT)
        return 2
    df = pd.read_parquet(OUT, columns=["trade_date"])
    mx = str(df["trade_date"].astype(str).max())
    clock = sorted(glob.glob(str(DAILY / "d_*.parquet")))
    asof = os.path.basename(clock[-1])[2:10] if clock else ""
    ok = mx == asof
    print("产物最新 %s / clock %s → %s" % (mx, asof, "OK" if ok else "落后"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐日打印覆盖情况（调度里不要开，120 行会淹掉日志）")
    a = ap.parse_args()
    if a.check:
        return check()

    res = build(a.days, a.verbose)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT, index=False)
    mx = str(res["trade_date"].max())
    nsec = res[res["trade_date"] == mx]["sector_code"].nunique()
    print("写出 %s：%d 行 / %d 个交易日 / %d 个行业 / 最新 %s"
          % (os.path.relpath(str(OUT), str(ROOT)).replace("\\", "/"),
             len(res), res["trade_date"].nunique(), nsec, mx))
    # 最近一日快照，便于人眼核对
    last = res[res["trade_date"] == mx].sort_values("ret")
    # ret 的单位是**百分数**（沿用 tushare pct_chg 口径：1.47 表示 1.47%），
    # 这里不再乘 100 —— 曾按 0.0147 的小数口径打印，直接把 3.97% 显示成 396.56%。
    print("最新交易日：最弱 %s %.2f%% / 最强 %s %.2f%% / 上涨行业 %d/%d"
          % (last.iloc[0]["sector_name"], last.iloc[0]["ret"],
             last.iloc[-1]["sector_name"], last.iloc[-1]["ret"],
             int((last["ret"] > 0).sum()), len(last)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
