# -*- coding: utf-8 -*-
"""回退预筛写入的 JEV 行（2026-10-30 BATCH4，K-ORG-20261030-BATCH4）。

背景：jev_prefilter 灰度期间 skip 行被写成 `bias=neutral, src=prefilter`，
且无回退路径；审计发现 17.8% 的待判条会被误杀（跌停/涨停/破产等）。
本工具默认 **dry-run**，只统计；`--apply` 才把匹配行的 jev 清空（交由 9B 重判）。

用法：
    python -B tools/jev_prefilter_revert.py --days 30            # 预览
    python -B tools/jev_prefilter_revert.py --days 30 --apply    # 执行
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEWS_DB = REPO / "news_collector" / "news.db"
NC = REPO / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

from jev_prefilter import is_prefilter_judgment  # noqa: E402


def find_candidates(conn: sqlite3.Connection, days: int = 30,
                    all_rows: bool = False) -> list[tuple]:
    sql = "SELECT id, created_at, source FROM news WHERE jev IS NOT NULL AND jev<>''"
    params: list = []
    if not all_rows:
        sql += " AND created_at >= date('now', '-%d day')" % int(days)
    rows = conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()
    out = []
    for nid, created_at, source in rows:
        jev = conn.execute("SELECT jev FROM news WHERE id=?", (nid,)).fetchone()[0]
        if is_prefilter_judgment(jev):
            out.append((nid, created_at, source))
    return out


def revert(conn: sqlite3.Connection, ids: list[str]) -> int:
    n = 0
    for nid in ids:
        cur = conn.execute("UPDATE news SET jev=NULL WHERE id=?", (nid,))
        n += int(cur.rowcount or 0)
    conn.commit()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回退预筛写入的 JEV 行（默认 dry-run）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="不按时间过滤（全量扫描）")
    ap.add_argument("--apply", action="store_true", help="真正清空（否则仅预览）")
    args = ap.parse_args(argv)
    if not NEWS_DB.exists():
        print("news.db 不存在：%s" % NEWS_DB)
        return 1
    conn = sqlite3.connect(str(NEWS_DB), timeout=30)
    try:
        rows = find_candidates(conn, days=args.days, all_rows=args.all)
        by_day: dict = {}
        for _nid, created_at, _src in rows:
            day = str(created_at or "")[:10]
            by_day[day] = by_day.get(day, 0) + 1
        print("候选（src=prefilter）%d 条；按日：%s"
              % (len(rows), json.dumps(by_day, ensure_ascii=False)))
        if not args.apply:
            print("DRY-RUN：未修改（确认后加 --apply）")
            return 0
        n = revert(conn, [r[0] for r in rows])
        print("已回退 %d 条（jev=NULL，待下轮 9B 重判）" % n)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())