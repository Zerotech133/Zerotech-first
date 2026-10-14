# -*- coding: utf-8 -*-
"""打板情绪周期 · 指标底座（K-STOCK-398 第一版·只读计算层）。

## 定位

打板/情绪周期策略的**数据面已完备**（K-STOCK-397 实证）：`limit_list_d` 字段含
`limit_times`(连板数) / `open_times`(炸板) / `up_stat`(连板统计"9/17") /
`first_time`·`last_time`(首末封) / `fd_amount`(封单额) / `float_mv`，
⇒ **tushare 打板套餐里的 `limit_step`(连板梯队) 本地已有等价数据，无需购买**。

本脚本把这些字段折成可直接消费的**日频情绪面板**，供门户策略页/预警使用。

## 输出（落 data/emotion/，不碰任何既有表）
- `ts_emotion_daily.parquet`：日频情绪面板
- `ts_emotion_ladder.parquet`：连板梯队明细（每日按连板高度分组）

## 指标定义（口径全部可审计）
| 指标 | 定义 |
|---|---|
| 涨停/跌停/炸板 | `limit` 字段 U/D/Z 计数 |
| 最高板 | 当日 `limit_times` 最大值 |
| 连板家数 | `limit_times >= 2` 的家数 |
| 首板数 | `limit_times == 1` |
| 封板率% | 涨停 / (涨停 + 炸板) × 100 |
| 晋级率% | 连板家数 / 前一交易日涨停家数 × 100 |
| 连板高度分布 | `limit_times` 分组计数 |
| 平均封单额 | `fd_amount` 均值（封板强度） |

## 纪律
- **只读** `data/tushare/limit_list_d`，只写 `data/emotion/`；
- 跳过 0 行文件与**非交易日**（trade_cal is_open=0）——tushare 对休市日会回退返回
  前一交易日数据，直接入库会污染情绪面板（398 判例）。
"""
from __future__ import annotations

import argparse
import json
import glob
import os
import sys
import time

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "tushare", "limit_list_d")
CAL = os.path.join(HERE, "data", "tushare", "trade_cal")
# K-STOCK-398：落 data/tdx/ 与生产 tdx 表同级 —— build_catalog.py 的 WAREHOUSES
# 只扫 data/tdx + data/tushare 两仓，落 data/emotion 会被目录构建器漏掉。
OUT = os.path.join(HERE, "data", "tdx")


def trading_days() -> set[str]:
    parts = sorted(glob.glob(os.path.join(CAL, "part_*.parquet")))
    if not parts:
        return set()
    cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"]) for p in parts])
    cal["cal_date"] = cal["cal_date"].astype(str)
    return set(cal[cal["is_open"] == 1]["cal_date"])


def load(src: str) -> pd.DataFrame:
    import pyarrow.parquet as pq
    ok = trading_days()
    frames = []
    for f in sorted(glob.glob(os.path.join(src, "d_*.parquet"))):
        day = os.path.basename(f)[2:10]
        if ok and day not in ok:            # 休市日跳过（398 判例）
            continue
        try:
            df = pq.read_table(f).to_pandas()
        except Exception:
            continue
        if len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = out["trade_date"].astype(str)
    return out


def build(df: pd.DataFrame):
    u = df[df["limit"] == "U"].copy()
    if u.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 日频面板
    g = df.groupby("trade_date").agg(
        涨停家数=("limit", lambda s: int((s == "U").sum())),
        跌停家数=("limit", lambda s: int((s == "D").sum())),
        炸板家数=("limit", lambda s: int((s == "Z").sum())),
    )
    lt = u.dropna(subset=["limit_times"]).copy()
    lt["limit_times"] = lt["limit_times"].astype(int)
    g["最高连板"] = lt.groupby("trade_date")["limit_times"].max()
    g["连板家数"] = lt[lt["limit_times"] >= 2].groupby("trade_date").size()
    g["首板家数"] = lt[lt["limit_times"] == 1].groupby("trade_date").size()
    g["封板率"] = (g["涨停家数"] / (g["涨停家数"] + g["炸板家数"]) * 100).round(2)
    g["晋级率"] = (g["连板家数"] / g["涨停家数"].shift(1) * 100).round(2)
    # 封板强度（fd_amount = 封单额）
    if "fd_amount" in u.columns:
        fa = u.groupby("trade_date")["fd_amount"].apply(
            lambda s: round(float(pd.to_numeric(s, errors="coerce").mean()), 2))
        g["平均封单额"] = fa
    g["总封单额"] = u.groupby("trade_date")["fd_amount"].apply(
        lambda s: float(pd.to_numeric(s, errors="coerce").sum())).round(0) \
        if "fd_amount" in u.columns else None
    g = g.fillna({"连板家数": 0, "首板家数": 0, "晋级率": 0})
    g.index.name = "trade_date"
    g = g.reset_index().sort_values("trade_date").reset_index(drop=True)

    # 连板梯队明细（≥2 板）
    ladder = lt[lt["limit_times"] >= 2][
        ["trade_date", "ts_code", "name", "industry", "limit_times",
         "open_times", "fd_amount", "float_mv", "first_time", "last_time"]
    ].copy() if all(c in lt.columns for c in
                    ("ts_code", "name", "industry", "open_times", "fd_amount",
                     "float_mv", "first_time", "last_time")) else pd.DataFrame()
    if not ladder.empty:
        ladder = ladder.sort_values(["trade_date", "limit_times"],
                                    ascending=[True, False]).reset_index(drop=True)
    return g, ladder


def _write_manifest(tbl: str, rows: int, df: pd.DataFrame) -> None:
    """写 data/tdx/_manifest.json 条目。

    🔴 判例：build_catalog.py 的 readiness_basis 默认 `file_presence`，
    manifest 缺条目时 catalog.json 里 `rows` 为 null（实测 ts_emotion_* 即如此）。
    写入后 catalog 才能报出真实行数。
    """
    mp = os.path.join(OUT, "_manifest.json")
    m = {}
    if os.path.exists(mp):
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            m = {}
    m[tbl] = {
        "offset": 0,
        "part": len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet"))),
        "rows": int(rows),
        "done": True,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "latest": str(df["trade_date"].max()) if "trade_date" in df.columns else None,
        "producer": "emotion_cycle.py (K-STOCK-398)",
    }
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, mp)


def main() -> int:
    ap = argparse.ArgumentParser(description="打板情绪周期指标底座（只读计算）")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--tail", type=int, default=15, help="打印末尾 N 行")
    args = ap.parse_args()

    df = load(SRC)
    if df.empty:
        print("★limit_list_d 无有效数据")
        return 1
    panel, ladder = build(df)

    print("=== 打板情绪面板 · %d 交易日（%s..%s）==="
          % (len(panel), panel["trade_date"].iloc[0], panel["trade_date"].iloc[-1]))
    cols = [c for c in ("trade_date", "涨停家数", "跌停家数", "炸板家数", "最高连板",
                        "连板家数", "首板家数", "封板率", "晋级率")
            if c in panel.columns]
    print(panel[cols].tail(args.tail).to_string(index=False))
    if "平均封单额" in panel.columns:
        print("\n平均封单额(万): %.2f  |  总封单额(万): %.2f"
              % (panel["平均封单额"].tail(args.tail).mean() / 1e4,
                 panel["总封单额"].tail(args.tail).mean() / 1e4))

    if not ladder.empty:
        top = ladder[ladder["limit_times"] >= ladder["limit_times"].max()]
        print("\n=== 当前最高板梯队（%d 板，共 %d 只）===" % (int(top["limit_times"].max()),
                                                  len(top)))
        cols2 = [c for c in ("ts_code", "name", "industry", "limit_times",
                             "open_times", "fd_amount", "first_time", "last_time")
                 if c in top.columns]
        print(top[cols2].head(12).to_string(index=False))

    if args.dryrun:
        print("\nDRY 未写盘")
        return 0

    # K-STOCK-398 修正：build_catalog.py 的扫描是「取 data/tdx 下的子**目录**」
    # （os.path.isdir 为真才收，跳过 `_` 开头），故产出必须是目录形态
    # （与 ts_ths_daily 等生产表一致），不能是裸 parquet 文件，否则目录构建器扫不到。
    print("\n已写：")
    for tbl, df in (("ts_emotion_daily", panel), ("ts_emotion_ladder", ladder)):
        tdir = os.path.join(OUT, tbl)
        os.makedirs(tdir, exist_ok=True)
        fp = os.path.join(tdir, "part_0000.parquet")
        df.to_parquet(fp, index=False)
        print("  %s  rows=%d  %s" % (fp, len(df), time.strftime("%H:%M:%S")))
        _write_manifest(tbl, len(df), df)
    print("完成 %s" % time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())