# -*- coding: utf-8 -*-
"""JEV 低置信复核队列（news.db 内建表，不新建服务）。

口径：news.jev 已判、conf < JEV_REVIEW_CONF（默认 0.7）且尚未入队。
用途：把 10.3% 低置信样本从“静默沉底”变成可检索、可裁决的队列。

用法：
  python jev_review_queue.py --build --limit 20000   # 首次建队列
  python jev_review_queue.py --status
  python jev_review_queue.py --list --limit 50
  python jev_review_queue.py --resolve <news_id> --decision correct --note "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "news.db"
DEFAULT_THRESHOLD = 0.7


def _threshold() -> float:
    try:
        return float(os.environ.get("JEV_REVIEW_CONF", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def ensure_queue(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jev_review_queue (
            id TEXT PRIMARY KEY,
            queued_at TEXT NOT NULL,
            conf REAL,
            bias TEXT,
            title TEXT,
            stocks TEXT,
            original_jev TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer TEXT,
            reviewed_at TEXT,
            decision TEXT,
            note TEXT
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jev_review_status "
        "ON jev_review_queue(status, queued_at DESC)"
    )
    conn.commit()


def _conf(jev: str) -> float | None:
    try:
        obj = json.loads(jev or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return float(obj.get("conf"))
    except (TypeError, ValueError):
        return None


def build(conn: sqlite3.Connection, limit: int = 20000,
          threshold: float | None = None) -> dict:
    ensure_queue(conn)
    threshold = _threshold() if threshold is None else float(threshold)
    rows = conn.execute(
        "SELECT id, ts, title, stocks, jev FROM news "
        "WHERE jev IS NOT NULL AND jev != '' ORDER BY ts DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    added = skipped = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for nid, _ts, title, stocks, jev in rows:
        conf = _conf(jev)
        if conf is None or conf >= threshold:
            continue
        obj = {}
        try:
            obj = json.loads(jev)
        except (TypeError, ValueError):
            pass
        cur = conn.execute(
            "INSERT OR IGNORE INTO jev_review_queue "
            "(id, queued_at, conf, bias, title, stocks, original_jev) "
            "VALUES (?,?,?,?,?,?,?)",
            (nid, now, conf, str(obj.get("bias") or ""), str(title or "")[:300],
             str(stocks or "[]"), str(jev)),
        )
        added += int(cur.rowcount or 0)
        skipped += int(not cur.rowcount)
    conn.commit()
    return {"threshold": threshold, "scanned": len(rows), "added": added,
            "skipped_existing": skipped}


def status(conn: sqlite3.Connection) -> dict:
    ensure_queue(conn)
    total = conn.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'"
    ).fetchone()[0]
    by_bias = conn.execute(
        "SELECT bias, COUNT(*) FROM jev_review_queue WHERE status='pending' "
        "GROUP BY bias ORDER BY 2 DESC"
    ).fetchall()
    return {"total": total, "pending": pending,
            "by_bias": [{"bias": b or "", "n": n} for b, n in by_bias]}


def list_pending(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    ensure_queue(conn)
    rows = conn.execute(
        "SELECT id, queued_at, conf, bias, title, status FROM jev_review_queue "
        "WHERE status='pending' ORDER BY conf ASC, queued_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(zip(("id", "queued_at", "conf", "bias", "title", "status"), r))
            for r in rows]


def resolve(conn: sqlite3.Connection, nid: str, decision: str,
            reviewer: str = "", note: str = "") -> bool:
    ensure_queue(conn)
    cur = conn.execute(
        "UPDATE jev_review_queue SET status='resolved', decision=?, reviewer=?, "
        "reviewed_at=?, note=? WHERE id=? AND status='pending'",
        (decision[:80], reviewer[:80], time.strftime("%Y-%m-%d %H:%M:%S"),
         note[:500], nid),
    )
    conn.commit()
    return bool(cur.rowcount)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resolve", default="")
    ap.add_argument("--decision", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--reviewer", default="")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)
    if not Path(args.db).exists():
        print("DB not found: %s" % args.db, file=sys.stderr)
        return 2
    conn = sqlite3.connect(args.db, timeout=30)
    try:
        if args.build:
            print(json.dumps(build(conn, args.limit if args.limit > 50 else 20000),
                             ensure_ascii=False))
        elif args.resolve:
            if not args.decision:
                print("--decision required", file=sys.stderr)
                return 2
            ok = resolve(conn, args.resolve, args.decision, args.reviewer, args.note)
            print(json.dumps({"ok": ok}, ensure_ascii=False))
        elif args.list:
            print(json.dumps(list_pending(conn, args.limit), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(status(conn), ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
