#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""龙虎榜 / 游资席位明细日快照采集（只读拉取 → 本地 jsonl → 原子落盘）。

## 为什么单独一个脚本

JEV 职责边界：采集层与渲染层物理隔离。本脚本 **输入 day、输出快照、无状态**，
可整体替换；`portal/jev_*.py` 只读快照、不联网。

## 两张表不是「详略两版」，是两个不同对象

  ts_top_list   个股级汇总（107,021 行）。**没有营业部字段**，只有 net/l_buy/l_sell。
  ts_hm_detail  席位级明细（151,838 行）。hm_orgs=营业部，hm_name=类型标签或市场绰号。

要问「谁在接」走 hm_detail；问「这票上了几次榜」走 top_list。

## 三条硬约束（来自 docs/JEV_口径定义_K5窗口_K8周期_席位边界_v1.md）

1. **不完整不落盘**：远端 total 与实取行数不一致就中止，绝不写半截文件。
   同族判例 #27——兜底数据写回本地会把残缺日「洗白」，之后永远不再补。
2. **不落空文件**：当日 0 行（非交易日 / 龙虎榜尚未披露）直接返回，不建空文件。
   空文件会被下一次幂等判据误读成「该日已补齐」。
3. **当日有 T+0 延迟**：实测 11-12 23:23 查当日两表均 0 行，而同刻 ts_limit_step
   已有数据。龙虎榜是交易所收盘后披露，**进不了盘中链路**——本脚本不适用于盘中调用。

## 用法

    python tools/top_list_fetch.py --day 20261111
    python tools/top_list_fetch.py --day 20261111 --tables top_list
    python tools/top_list_fetch.py --day 20261111 --force     # 行数一致也重取

退出码：0=正常（含当日 0 行）；2=取数不完整/异常（未落盘）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

TABLES = {
    # 目录名 -> (远端表名, 说明)
    "top_list": ("ts_top_list", "龙虎榜·个股级汇总（无营业部字段）"),
    "hm_detail": ("ts_hm_detail", "龙虎榜·席位明细（hm_orgs 营业部 / hm_name 绰号）"),
}


def _norm_day(day: str) -> str:
    d = str(day).strip().replace("-", "")
    if len(d) != 8 or not d.isdigit():
        raise SystemExit("--day 需为 YYYYMMDD 或 YYYY-MM-DD，收到 %r" % day)
    return d


def fetch_one(client, sub: str, day: str, limit: int = 5000):
    """取一日一表。返回 (rows, remote_total)。不完整则抛 SystemExit(2)。"""
    tbl = TABLES[sub][0]
    j = client.table(tbl, start_date=day, end_date=day, limit=limit)
    rows = j.get("rows") or []
    cols = j.get("columns") or []
    total = j.get("total")
    # total 语义：该日过滤后的行数（实测 11-11 top_list=75 / hm_detail=209）。
    # 与 ts_stk_mins 不同——那张表的 total 是该 code 的全量行数（坑 3）。
    if total is not None and len(rows) != total:
        raise SystemExit(
            "[FAIL] %s %s 取数不完整：remote_total=%s 实取=%d（limit=%d）——拒绝落盘"
            % (tbl, day, total, len(rows), limit)
        )
    return [dict(zip(cols, r)) for r in rows], total


def write_jsonl(path: str, rows: list) -> None:
    """原子写：先 .tmp，再 os.replace。写前若已有文件则备份 .bak。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if os.path.exists(path):
        bak = path + ".bak"
        try:
            os.replace(path, bak)
        except OSError:
            pass  # 备份失败不阻断主写入
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--tables", default="top_list,hm_detail")
    ap.add_argument("--force", action="store_true", help="行数一致也重取")
    a = ap.parse_args()

    day = _norm_day(a.day)
    subs = [s.strip() for s in a.tables.split(",") if s.strip()]
    bad = [s for s in subs if s not in TABLES]
    if bad:
        raise SystemExit("未知表 %r，可选 %s" % (bad, list(TABLES)))

    from xtcloud_client import XtCloud  # noqa: E402  延迟导入，--help 不依赖网络栈

    c = XtCloud()
    report = {"day": day, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tables": {}}

    for sub in subs:
        path = os.path.join(ROOT, "data", sub, "%s.jsonl" % day)
        prev = 0
        if os.path.exists(path) and not a.force:
            with open(path, encoding="utf-8") as f:
                prev = sum(1 for _ in f)
        rows, total = fetch_one(c, sub, day)

        if not rows:
            # 约束 2：不落空文件
            report["tables"][sub] = {
                "status": "empty",
                "remote_total": total,
                "note": "当日 0 行（非交易日或龙虎榜未披露），未建文件",
            }
            continue

        if prev == len(rows) and not a.force:
            report["tables"][sub] = {"status": "skip", "rows": len(rows), "note": "行数一致，跳过"}
            continue

        write_jsonl(path, rows)
        report["tables"][sub] = {
            "status": "written",
            "rows": len(rows),
            "path": os.path.relpath(path, ROOT).replace("\\", "/"),
        }

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
