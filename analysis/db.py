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
    with conn() as c:
        for e in events:
            c.execute("""INSERT OR REPLACE INTO events(event_id,code,date,category,title,url,pdf,text_len,confidence,reviewed,facts)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (e["event_id"], e["code"], e["date"], e["category"], e["title"], e.get("url", ""),
                       e.get("pdf", ""), e.get("text_len", 0), e.get("confidence", 0.0),
                       1 if e.get("reviewed") else 0, json.dumps(e.get("facts", {}), ensure_ascii=False)))


def load_events(code, limit=80):
    with conn() as c:
        rows = c.execute("SELECT * FROM events WHERE code=? ORDER BY date DESC LIMIT ?", (code, limit)).fetchall()
    cols = ["event_id", "code", "date", "category", "title", "url", "pdf", "text_len", "confidence", "reviewed", "facts"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        try:
            d["facts"] = json.loads(d["facts"] or "{}")
        except Exception:
            d["facts"] = {}
        out.append(d)
    return out
