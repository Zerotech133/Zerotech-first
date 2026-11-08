# -*- coding: utf-8 -*-
r"""新闻传播关系回填：原创 / 首发 / 转发 / 采集重复 / 周期重播。

给 news 表补 5 个字段并全量判定（老田 2026-11-08 令：要分原创、首发、转发）。

    python -X utf8 tools/news_origin_backfill.py --dry-run   # 只统计，不写库
    python -X utf8 tools/news_origin_backfill.py             # 建字段 + 全量回填
    python -X utf8 tools/news_origin_backfill.py --limit 5000

口径与阈值见 news_collector/news_origin.py 的模块 docstring（同一次实测得出）。
幂等：可反复跑，结果只取决于当前库内容。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "news_collector"))

import news_origin as O  # noqa: E402

NEWS_DB = os.path.join(ROOT, "news_collector", "news.db")

COLUMNS = [
    ("origin_kind", "TEXT"),     # original / first / repost / dup / recur / retell
    ("origin_fp", "TEXT"),       # 文章指纹（id hash 优先）
    ("repost_of", "TEXT"),       # 首发 id（repost / retell 时有值）
    ("lag_min", "INTEGER"),      # 相对首发的分钟差
    ("origin_ev", "TEXT"),       # 判定依据（人话）
]


def ensure_columns(con) -> list:
    """补齐字段与索引；返回本次新建的列名（已存在则空）。"""
    have = {r[1] for r in con.execute("PRAGMA table_info(news)")}
    added = []
    for name, typ in COLUMNS:
        if name not in have:
            con.execute("ALTER TABLE news ADD COLUMN %s %s" % (name, typ))
            added.append(name)
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_ofp ON news(origin_fp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_okind ON news(origin_kind)")
    con.commit()
    return added


def load_rows(con, limit: int = 0):
    sql = ("SELECT id, source, ts, title, content FROM news "
           "WHERE title IS NOT NULL AND title!=''")
    if limit:
        sql += " LIMIT %d" % int(limit)
    return [dict(id=r[0], source=r[1], ts=r[2], title=r[3], content=r[4] or "")
            for r in con.execute(sql)]


def bucket_by_title(rows):
    g = defaultdict(list)
    for r in rows:
        fp = O.title_fp(r["title"])
        if fp:
            g[fp].append(r)
    return g


def backfill(con, limit: int = 0, dry: bool = False) -> dict:
    t0 = time.time()
    added = [] if dry else ensure_columns(con)
    rows = load_rows(con, limit=limit)
    groups = bucket_by_title(rows)

    verdicts, ledger = [], []
    for fp, items in groups.items():
        for v in O.classify_group(items):
            verdicts.append(v)
            # origin_fp 写「标题组指纹」而不是文章指纹：文章指纹每条各不相同
            # （i:sina:xxx / i:wsc:yyy），写它就无法一查拿出整条传播链。
            # 组指纹才让 `WHERE origin_fp=?` 直接列出「谁先发、谁转、差多久」。
            ledger.append((v["kind"], fp, v["repost_of"], v["lag_min"],
                           v["ev"], v["id"]))
    dist = Counter(v["kind"] for v in verdicts)
    by_src = Counter()
    for v in verdicts:
        if v["kind"] in ("repost", "dup", "recur"):
            row = next((r for r in rows if r["id"] == v["id"]), None)
            if row:
                by_src[row["source"]] += 1
    lag_med = None
    lags = sorted(v["lag_min"] for v in verdicts
                  if v["kind"] == "repost" and v["lag_min"] is not None)
    if lags:
        lag_med = lags[len(lags) // 2]

    if not dry:
        con.executemany(
            "UPDATE news SET origin_kind=?, origin_fp=?, repost_of=?,"
            " lag_min=?, origin_ev=? WHERE id=?", ledger)
        con.commit()

    return {
        "ok": True, "dry": dry, "rows": len(rows), "groups": len(groups),
        "added_columns": added, "dist": dict(dist),
        "repost_lag_median_min": lag_med,
        "by_source": dict(by_src.most_common(8)),
        "seconds": round(time.time() - t0, 2),
    }


def sample(con, kind: str, n: int = 5) -> list:
    """抽样：某类判定的实际行（看依据是否站得住）。"""
    out = []
    for r in con.execute(
            "SELECT id, ts, source, title, origin_ev, repost_of FROM news"
            " WHERE origin_kind=? ORDER BY ts DESC LIMIT ?", (kind, n)):
        out.append({"id": r[0], "ts": r[1], "source": r[2],
                    "title": (r[3] or "")[:52], "ev": r[4], "repost_of": r[5]})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="新闻传播关系回填")
    p.add_argument("--dry-run", action="store_true", help="只统计，不建字段不写库")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 行（调试用）")
    p.add_argument("--show", default="", help="抽样展示某类：repost/dup/recur/retell/first/original")
    a = p.parse_args(argv)

    if not os.path.isfile(NEWS_DB):
        print(json.dumps({"ok": False, "reason": "news.db 不存在"}, ensure_ascii=False))
        return 2
    con = sqlite3.connect(NEWS_DB, timeout=30)
    try:
        if a.show:
            print(json.dumps(sample(con, a.show, 8), ensure_ascii=False, indent=1))
            return 0
        res = backfill(con, limit=a.limit, dry=a.dry_run)
    finally:
        con.close()
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
