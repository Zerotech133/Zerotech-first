# -*- coding: utf-8 -*-
"""事件人工复核队列（CLI）。

事件由公告规则分类自动入库（reviewed=0），本工具用于人工复核：
  python -m analysis.review --code 600337.SH --list           # 查看待复核
  python -m analysis.review --code 600337.SH --interactive    # 逐条复核（y/n/c/s/q）
  python -m analysis.review --approve <event_id> [--category 重整] [--note "确认"]
  python -m analysis.review --reject  <event_id> [--note "误分类"]
  python -m analysis.review --stats [--code 600337.SH]

状态：reviewed = 1 通过 / -1 驳回 / 0 待复核；category_final 为人工修正后的类别。
"""
import argparse
import json
import os
import sqlite3
import time

from . import config, db
import logging

EXT_COLS = (("reviewed_at", "TEXT"), ("review_note", "TEXT"), ("category_final", "TEXT"))


def connect(path=None):
    p = path or config.DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    for col, typ in EXT_COLS:
        try:
            con.execute("ALTER TABLE events ADD COLUMN %s %s" % (col, typ))
        except sqlite3.OperationalError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/review.py", exc)
    return con


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def pending(code=None, limit=100, days=365, path=None):
    con = connect(path)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    sql = "SELECT * FROM events WHERE reviewed=0 AND date>=?"
    args = [cutoff]
    if code:
        sql += " AND code=?"
        args.append(code)
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(limit)
    rows = _rows(con, sql, args)
    con.close()
    return rows


def list_events(code=None, limit=100, status="all", path=None):
    con = connect(path)
    sql = "SELECT * FROM events WHERE 1=1"
    args = []
    if code:
        sql += " AND code=?"
        args.append(code)
    if status == "pending":
        sql += " AND reviewed=0"
    elif status == "approved":
        sql += " AND reviewed=1"
    elif status == "rejected":
        sql += " AND reviewed=-1"
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(limit)
    rows = _rows(con, sql, args)
    con.close()
    return rows


def apply(event_id, action, category=None, note=None, path=None):
    con = connect(path)
    row = con.execute("SELECT event_id, category FROM events WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        con.close()
        return {"ok": False, "error": "event 不存在: " + str(event_id)}
    reviewed = 1 if action == "approve" else -1 if action == "reject" else 0
    cat_final = category or row["category"]
    con.execute("UPDATE events SET reviewed=?, reviewed_at=?, review_note=?, category_final=? WHERE event_id=?",
                (reviewed, time.strftime("%F %T"), note or "", cat_final, event_id))
    con.commit()
    con.close()
    return {"ok": True, "event_id": event_id, "reviewed": reviewed, "category_final": cat_final}


def stats(code=None, path=None):
    con = connect(path)
    where = " WHERE code=?" if code else ""
    args = (code,) if code else ()
    total = con.execute("SELECT COUNT(*) FROM events" + where, args).fetchone()[0]
    pending_n = con.execute("SELECT COUNT(*) FROM events" + where + (" AND" if where else " WHERE") + " reviewed=0", args).fetchone()[0]
    approved = con.execute("SELECT COUNT(*) FROM events" + where + (" AND" if where else " WHERE") + " reviewed=1", args).fetchone()[0]
    rejected = con.execute("SELECT COUNT(*) FROM events" + where + (" AND" if where else " WHERE") + " reviewed=-1", args).fetchone()[0]
    by_cat = _rows(con, "SELECT category, COUNT(*) n FROM events" + where + " GROUP BY category ORDER BY n DESC", args)
    con.close()
    return {"code": code, "total": total, "pending": pending_n, "approved": approved,
            "rejected": rejected, "by_category": by_cat}


def _print_row(e, idx=None):
    flag = {1: "✓", -1: "✗"}.get(e.get("reviewed"), "·")
    cat = e.get("category_final") or e.get("category")
    prefix = "%2d. " % idx if idx else ""
    print("%s[%s] %s  %s  %s" % (prefix, flag, e.get("date"), cat, (e.get("title") or "")[:70]))
    if e.get("url"):
        print("      ", e["url"][:110])


def interactive(code=None, limit=100):
    items = pending(code=code, limit=limit)
    if not items:
        print("（无待复核事件）")
        return {"ok": True, "reviewed": 0}
    done = 0
    for i, e in enumerate(items, 1):
        print("\n" + "-" * 60)
        _print_row(e, i)
        print("      原文：", (e.get("url") or "")[:110])
        try:
            c = input("复核 [y]通过 [n]驳回 [c]改类别 [s]跳过 [q]退出 > ").strip().lower()
        except EOFError:
            break
        if c == "q":
            break
        if c == "s" or not c:
            continue
        if c in ("y", "n"):
            apply(e["event_id"], "approve" if c == "y" else "reject")
            done += 1
        elif c == "c":
            newcat = input("新类别（如 重整/退市/ST/业绩/减持/增持/回购/质押/解禁/诉讼/问询/监管/其他）> ").strip()
            note = input("备注（可空）> ").strip()
            apply(e["event_id"], "approve", category=newcat or None, note=note or None)
            done += 1
    print("\n已复核 %d 条" % done)
    return {"ok": True, "reviewed": done, "left": len(pending(code=code, limit=limit))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default="all", choices=["all", "pending", "approved", "rejected"])
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--approve", default=None)
    ap.add_argument("--reject", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    a = ap.parse_args()

    if a.approve:
        print(json.dumps(apply(a.approve, "approve", a.category, a.note), ensure_ascii=False))
    elif a.reject:
        print(json.dumps(apply(a.reject, "reject", a.category, a.note), ensure_ascii=False))
    elif a.stats:
        print(json.dumps(stats(a.code), ensure_ascii=False, indent=1))
    elif a.interactive:
        interactive(a.code, a.limit)
    else:
        rows = list_events(a.code, a.limit, a.status)
        for i, e in enumerate(rows, 1):
            _print_row(e, i)
        print("\n共 %d 条（status=%s）" % (len(rows), a.status))
        print(json.dumps(stats(a.code), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
