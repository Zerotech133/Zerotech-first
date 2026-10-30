# -*- coding: utf-8 -*-
"""curated 行情表日更工具（P0S）：日更补缺交易日（daily/daily_basic/moneyflow/index_daily）。

2026-10-30 IDXDAILY：加入 index_daily。背景：TushareSync 只更新 per-code 缓存、curated
单文件表滞后；原先 daily 三表有日更，但 **index_daily 只在全量重建时刷新**，导致
index_daily 冻结在 10-23、沪深300 基准缺失 10-24 起所有交易日 → JEV 后验/校准无法推进。

背景：TushareSync 每晚只更新 per-code 缓存，curated 单文件表会滞后（实测 10-23→10-29）。
本工具只做 **append-only**：按交易日把缺失行情补进 curated 单文件；不删除、不改历史行。
安全规则：
  - 只补 trade_cal 已收盘的交易日（today 需 ≥ --include-today-hour，默认 18 点才含当日）；
  - 每表只追加 (ts_code, trade_date) 不在旧表中的行；旧表内重复行保持原样（不做全局去重，避免误删历史修订）；
  - 失败/无数据 → 非 0 退出 + 日志，供定时任务告警。
用法：
  python -m analysis.curated_update --auto              # 自动补缺（计划任务用）
  python -m analysis.curated_update --auto --dry-run    # 只打印计划
  python -m analysis.curated_update --dates 20261030    # 指定日期
"""
import argparse
import json
import os
import sys
import time

import pandas as pd

from . import config

CURATED = os.environ.get("ANALYSIS_CURATED_OVERRIDE") or config.CURATED
TABLES = ["daily", "daily_basic", "moneyflow", "index_daily"]
KEY = ["ts_code", "trade_date"]


def _ts_call(api, params):
    sys.path.insert(0, os.path.join(config.MAIN_ROOT, "quant_backtest"))
    import tushare_sync as T
    return T.ts_call(api, params)


def _df(r):
    if isinstance(r, pd.DataFrame):
        return r
    if isinstance(r, dict) and r.get("fields"):
        return pd.DataFrame(r.get("items") or [], columns=r["fields"])
    return pd.DataFrame(r or [])


def missing_dates(tables=None, today=None, include_today_hour=18):
    """返回各表需要补的交易日列表（dict: table -> [YYYYMMDD]）。"""
    tables = tables or TABLES
    today = today or time.strftime("%Y%m%d")
    now_hour = time.localtime().tm_hour
    cal = pd.read_parquet(os.path.join(CURATED, "trade_cal.parquet"))
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    out = {}
    for t in tables:
        p = os.path.join(CURATED, t + ".parquet")
        if not os.path.exists(p):
            out[t] = []
            continue
        last = str(pd.read_parquet(p, columns=["trade_date"])["trade_date"].max())[:8]
        need = [d for d in open_days if d > last]
        # 当日收盘后数据未必就绪：默认 18 点后才纳入当天
        need = [d for d in need if d < today or (d == today and now_hour >= include_today_hour)]
        out[t] = need
    return out


def append_table(table, dates, dry_run=False):
    p = os.path.join(CURATED, table + ".parquet")
    old = pd.read_parquet(p)
    have = set(zip(old["ts_code"].astype(str), old["trade_date"].astype(str)))
    parts = []
    for d in dates:
        df = _df(_ts_call(table, {"trade_date": d}))
        if not len(df):
            print("[warn] %s %s 无数据" % (table, d), flush=True)
            continue
        df["ts_code"] = df["ts_code"].astype(str)
        df["trade_date"] = df["trade_date"].astype(str)
        mask = [not (c, dd) in have for c, dd in zip(df["ts_code"], df["trade_date"])]
        new = df.loc[mask]
        print("[fetch] %s %s rows=%d new=%d" % (table, d, len(df), len(new)), flush=True)
        if len(new):
            parts.append(new)
    if not parts:
        return 0
    add = pd.concat(parts, ignore_index=True)
    if dry_run:
        print("[dry-run] %s 将追加 %d 行" % (table, len(add)), flush=True)
        return len(add)
    df = pd.concat([old, add], ignore_index=True).sort_values(KEY).reset_index(drop=True)
    tmp = p + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    print("[append] %s: %d -> %d (+%d)" % (table, len(old), len(df), len(df) - len(old)), flush=True)
    return len(df) - len(old)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--dates", default="")
    ap.add_argument("--tables", default=",".join(TABLES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-today-hour", type=int, default=18)
    a = ap.parse_args()
    tables = [t.strip() for t in a.tables.split(",") if t.strip()]
    if a.dates:
        plan = {t: [a.dates] for t in tables}
    elif a.auto:
        plan = missing_dates(tables, include_today_hour=a.include_today_hour)
    else:
        ap.print_help()
        return 2
    print(json.dumps({"plan": plan, "dry_run": a.dry_run}, ensure_ascii=False), flush=True)
    total = 0
    rc = 0
    for t, dates in plan.items():
        if not dates:
            print("[skip] %s 无缺失交易日" % t, flush=True)
            continue
        n = append_table(t, dates, dry_run=a.dry_run)
        total += n
        if n == 0:
            rc = 1  # 有计划但没补到 → 视为异常（供定时任务告警）
    out = {"ok": rc == 0, "appended": total, "tables": tables}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
