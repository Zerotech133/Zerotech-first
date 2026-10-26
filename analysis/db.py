# -*- coding: utf-8 -*-
"""SQLite：run 台账 + 事件库。"""
import json
import os
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, code TEXT, name TEXT, as_of TEXT,
  started TEXT, finished TEXT, status TEXT DEFAULT 'running',
  rating TEXT, coverage REAL, artifacts_dir TEXT, error TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, code TEXT, date TEXT, category TEXT,
  title TEXT, url TEXT, pdf TEXT, text_len INTEGER DEFAULT 0,
  confidence REAL DEFAULT 0, reviewed INTEGER DEFAULT 0, facts TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_code ON events(code, date);
"""


def conn():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    c = sqlite3.connect(config.DB_PATH)
    c.executescript(SCHEMA)
    return c


def new_run(code, name, as_of):
    run_id = "%s-%s-%s" % (code.replace(".", ""), as_of.replace("-", ""), time.strftime("%H%M%S"))
    with conn() as c:
        c.execute("INSERT INTO runs(run_id,code,name,as_of,started,status) VALUES(?,?,?,?,?,'running')",
                  (run_id, code, name, as_of, time.strftime("%F %T")))
    return run_id


def finish_run(run_id, status="ok", rating=None, coverage=None, artifacts_dir=None, error=None, meta=None):
    with conn() as c:
        c.execute("UPDATE runs SET finished=?,status=?,rating=?,coverage=?,artifacts_dir=?,error=?,meta=? WHERE run_id=?",
                  (time.strftime("%F %T"), status, rating, coverage, artifacts_dir, error,
                   json.dumps(meta or {}, ensure_ascii=False), run_id))


def upsert_events(events):
    """幂等写入；保留人工复核状态（reviewed/category_final/reviewed_at/review_note）。"""
    with conn() as c:
        for e in events:
            c.execute("""INSERT INTO events(event_id,code,date,category,title,url,pdf,text_len,confidence,reviewed,facts)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(event_id) DO UPDATE SET
                           code=excluded.code, date=excluded.date, category=excluded.category,
                           title=excluded.title, url=excluded.url, pdf=excluded.pdf,
                           text_len=excluded.text_len, confidence=excluded.confidence, facts=excluded.facts""",
                      (e["event_id"], e["code"], e["date"], e["category"], e["title"], e.get("url", ""),
                       e.get("pdf", ""), e.get("text_len", 0), e.get("confidence", 0.0),
                       0, json.dumps(e.get("facts", {}), ensure_ascii=False)))


def load_events(code, limit=80):
    with conn() as c:
        cur = c.execute("SELECT * FROM events WHERE code=? ORDER BY date DESC LIMIT ?", (code, limit))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        try:
            d["facts"] = json.loads(d["facts"] or "{}")
        except Exception:
            d["facts"] = {}
        out.append(d)
    return out
