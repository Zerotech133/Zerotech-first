# -*- coding: utf-8 -*-
"""JEV 低置信复核 API 回归：列表筛选/排序/裁决写回。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))
import api_jev_review as R  # noqa: E402


def _db(tmp_path, monkeypatch):
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute("""CREATE TABLE jev_review_queue (
        id TEXT PRIMARY KEY, queued_at TEXT, conf REAL, bias TEXT, title TEXT,
        stocks TEXT, original_jev TEXT, status TEXT DEFAULT 'pending',
        reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)""")
    rows = [
        ("a", "2026-10-30 10:00", 0.3, "bull", "A"),
        ("b", "2026-10-30 10:01", 0.6, "bear", "B"),
        ("c", "2026-10-30 10:02", 0.5, "bull", "C"),
    ]
    con.executemany(
        "INSERT INTO jev_review_queue (id,queued_at,conf,bias,title) VALUES (?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    monkeypatch.setattr(R, "NEWS_DB", str(p))
    return p


def test_query_sort_filter_paging(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    d = R.query(sort="conf_asc")
    assert [x["id"] for x in d["items"]] == ["a", "c", "b"]
    assert d["summary"]["pending"] == 3
    d = R.query(bias="bull")
    assert {x["id"] for x in d["items"]} == {"a", "c"}
    d = R.query(limit=1, offset=1)
    assert d["returned"] == 1


def test_resolve_writes_back(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    r = R.resolve("c", "correct", "老田", "维持")
    assert r["ok"] and r["item"]["status"] == "resolved"
    assert r["item"]["reviewer"] == "老田"
    assert r["item"]["decision"] == "correct"
    assert (r["item"]["reviewed_at"] or "")
    assert not R.resolve("c", "wrong", "老田")["ok"]
    try:
        R.resolve("a", "bad", "老田")
    except ValueError:
        pass
    else:
        raise AssertionError("非法 decision 应拒绝")
