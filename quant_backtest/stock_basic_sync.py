#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临界证券基准全集同步（public.stock_basic）—— X2 实体映射的前置管线。

设计稿口径（docs/plans/2026-10-08_X2实体ID映射_设计稿.md）：
  xmap init 基准优先级 = PG public.stock_basic.symbol；本表此前无灌库脚本
  （2026-10-09 发现为空），本脚本补齐该缺口。

数据源（全本地，零外网）：
  1. 代码全集 = 特征湖 quant_backtest/data/ashare/*.parquet 文件名
     （000001_SZ.parquet → 000001.SZ，K254 后复权日线湖，5244 只）
  2. 名称等元数据 = portal/qd_daily_data/snap_最新.json（qd_daily_snapshot
     每日 14:55 全市场快照，5244 只含 name）

写入：TRUNCATE + 全量 INSERT（基准表 ~5.5k 行小表，事务内原子刷新）。
幂等：可重复执行；行情湖与快照差集如实报告（各自覆盖面不同属正常）。

用法（系统 Python311，需 psycopg2）：
  python quant_backtest/stock_basic_sync.py            # 同步并输出统计
  python quant_backtest/stock_basic_sync.py --dry-run  # 只报差异不写库
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASHARE_DIR = HERE / "data" / "ashare"
SNAP_DIR = HERE.parent / "portal" / "qd_daily_data"

PG = dict(host=os.environ.get("XMAP_PG_HOST", "127.0.0.1"),
          port=int(os.environ.get("XMAP_PG_PORT", "5442")),
          user=os.environ.get("XMAP_PG_USER", "quant_user"),
          password=os.environ.get("XMAP_PG_PASSWORD", "quant_password_123"),
          dbname=os.environ.get("XMAP_PG_DB", "quant_system"))


def codes_from_lake() -> dict[str, dict]:
    """特征湖文件名 → {ts_code: {}}（湖即全集口径）。"""
    out = {}
    for f in glob.glob(str(ASHARE_DIR / "*.parquet")):
        stem = Path(f).stem          # 000001_SZ
        if "_" not in stem or len(stem) < 6:
            continue
        code, mkt = stem.split("_", 1)
        if mkt in ("SH", "SZ", "BJ") and code.isdigit():
            out[f"{code}.{mkt}"] = {}
    return out


def names_from_snapshot() -> dict[str, str]:
    """最新 qd 快照 → {ts_code: name}（缺失返回空 dict，名称列留空不阻塞）。"""
    snaps = sorted(SNAP_DIR.glob("snap_*.json"))
    if not snaps:
        return {}
    try:
        d = json.loads(snaps[-1].read_text(encoding="utf-8"))
        rows = d.get("rows") or {}
        return {k.replace("_", "."): (v or {}).get("name", "")
                for k, v in rows.items() if k.count("_") == 1}
    except Exception as e:  # noqa
        print(f"[warn] 快照解析失败（{snaps[-1].name}）：{e}", file=sys.stderr)
        return {}


def main(argv=None) -> int:
    dry = "--dry-run" in (argv or sys.argv[1:])
    lake = codes_from_lake()
    names = names_from_snapshot()
    if not lake:
        print("[error] 特征湖无 parquet：", ASHARE_DIR, file=sys.stderr)
        return 1
    missing_name = [c for c in lake if c not in names]
    extra_name = [c for c in names if c not in lake]
    print(f"特征湖全集 {len(lake)} | 快照覆盖 {len(names)}"
          f" | 湖有快照无 {len(missing_name)} | 快照有湖无 {len(extra_name)}")
    if dry:
        print("[dry-run] 不写库。")
        return 0

    import psycopg2
    conn = psycopg2.connect(connect_timeout=5, **PG)
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE public.stock_basic")
            now = datetime.now()
            rows = []
            for c in sorted(lake):
                nm = names.get(c, "")
                rows.append((c, nm, c.split(".")[-1],
                             ("ST" in nm.upper()), False, False, now))
            cur.executemany(
                "INSERT INTO public.stock_basic"
                " (symbol, name, market, is_st, is_suspended, is_delisted,"
                "  updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                rows)
            cur.execute("SELECT count(*) FROM public.stock_basic")
            n = cur.fetchone()[0]
        conn.commit()
        print(f"[ok] public.stock_basic 已写入 {n} 行"
              f"（名称缺失 {len(missing_name)} 行留空）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
