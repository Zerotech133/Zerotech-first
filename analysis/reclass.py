# -*- coding: utf-8 -*-
"""事件旧类别清洗（P0U）。

背景：事件库存在历史规则分类残留（如 2026-09 重整投资协议仍标"退市/ST"），
      报告按 category 展示会误导。本工具按**当前** config.EVENT_RULES 重算 category，
      只更新 category 列；**保留 reviewed / category_final / review_note / reviewed_at**（人工结论不动）。
用法：
  python -m analysis.reclass --dry-run        # 只看差异
  python -m analysis.reclass --apply          # 落库（自动备份 DB）
  python -m analysis.reclass --apply --code 600337.SH
"""
import argparse
import os
import shutil
import sqlite3
import time

from . import config, db, events

SCHEMA = db.SCHEMA + """
"""


def _connect(path=None):
    p = path or config.DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    cols = {row[1] for row in c.execute("PRAGMA table_info(events)")}
    for _col in ("category_final", "review_note", "reviewed_at"):
        if _col not in cols:
            c.execute("ALTER TABLE events ADD COLUMN %s TEXT" % _col)
    c.commit()
    return c


def plan(path=None, code=None, limit=None):
    con = _connect(path)
    sql = "SELECT event_id, code, date, category, category_final, title, reviewed FROM events"
    args = []
    if code:
        sql += " WHERE code=?"
        args.append(code)
    sql += " ORDER BY date DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    rows = con.execute(sql, args).fetchall()
    con.close()
    diffs = []
    for r in rows:
        new = events.classify(r["title"] or "")
        old = r["category"] or ""
        if new != old:
            diffs.append({"event_id": r["event_id"], "code": r["code"], "date": r["date"],
                          "title": (r["title"] or "")[:70], "old": old, "new": new,
                          "reviewed": r["reviewed"], "category_final": r["category_final"]})
    return diffs


def apply(diffs, path=None, backup=True):
    if not diffs:
        return {"ok": True, "updated": 0, "backup": None}
    bak = None
    if backup:
        p = path or config.DB_PATH
        ts = time.strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(config.WORK, "backup")
        os.makedirs(bdir, exist_ok=True)
        bak = os.path.join(bdir, "analysis_before_reclass_%s.db" % ts)
        shutil.copy2(p, bak)
    con = _connect(path)
    c = con.cursor()
    for d in diffs:
        c.execute("UPDATE events SET category=? WHERE event_id=?", (d["new"], d["event_id"]))
    con.commit()
    con.close()
    return {"ok": True, "updated": len(diffs), "backup": bak}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--code", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args()
    diffs = plan(code=a.code or None, limit=a.limit or None)
    from collections import Counter
    pairs = Counter("%s → %s" % (d["old"], d["new"]) for d in diffs)
    print("differences:", len(diffs))
    for k, v in pairs.most_common(15):
        print("  %-24s %d" % (k, v))
    for d in diffs[:a.show]:
        print(" -", d["date"], "|", d["old"], "→", d["new"], "|", d["title"])
    if a.apply and diffs:
        r = apply(diffs)
        print("APPLIED:", r)
    elif a.dry_run:
        print("dry-run only（加 --apply 落库）")


if __name__ == "__main__":
    main()
