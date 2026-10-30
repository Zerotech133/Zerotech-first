# -*- coding: utf-8 -*-
"""增量事件流（since/cursor）测试：纯函数 + 临时 news.db 集成。"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from portal import api_jev  # noqa: E402


def _mkdb(tmp_path):
    db = tmp_path / "news.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE news (id TEXT PRIMARY KEY, ts TEXT, created_at TEXT,"
                 " source TEXT, title TEXT, stocks TEXT, jev TEXT)")
    rows = [
        ("a", "2026-10-30 09:00:00", "2026-10-30 09:00:01", "sina", "早间利好",
         "[]", json.dumps({"bias": "bull", "conf": 0.8, "event_type": "price"}, ensure_ascii=False)),
        ("b", "2026-10-30 10:00:00", "2026-10-30 10:00:05", "sina", "盘中利空",
         "[]", json.dumps({"bias": "bear", "conf": 0.9, "event_type": "risk"}, ensure_ascii=False)),
        ("c", "2026-10-30 11:00:00", "2026-10-30 11:00:05", "eastmoney", "午间利好",
         "[]", json.dumps({"bias": "bull", "conf": 0.7, "event_type": "policy"}, ensure_ascii=False)),
    ]
    conn.executemany("INSERT INTO news VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def test_normalize_since():
    assert api_jev._normalize_since("2026-10-30T10:00:00") == "2026-10-30 10:00:00"
    assert api_jev._normalize_since("") == ""
    try:
        api_jev._normalize_since("not-a-date")
    except ValueError:
        pass
    else:
        raise AssertionError("非法 since 应 ValueError")


def test_query_incremental_cursor(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    monkeypatch.setattr(api_jev, "NEWS_DB", str(db))
    monkeypatch.setattr(api_jev, "_latest_meta", lambda: (2.5, set()))
    r1 = api_jev.query(since="2026-10-30 09:30:00", limit=1)
    assert r1["since"] == "2026-10-30 09:30:00"
    assert len(r1["items"]) == 1 and r1["items"][0]["id"] == "b"
    assert r1["cursor"] == "2026-10-30 10:00:05"
    r2 = api_jev.query(since=r1["cursor"], limit=5)
    assert [it["id"] for it in r2["items"]] == ["b", "c"]  # 边界同秒包含，按 id 去重


def test_query_default_window_unchanged(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    monkeypatch.setattr(api_jev, "NEWS_DB", str(db))
    monkeypatch.setattr(api_jev, "_latest_meta", lambda: (2.5, set()))
    r = api_jev.query(hours=48, now=dt.datetime(2026, 10, 31, 0, 0, 0), limit=10)
    assert r["since"] is None and r["cursor"] is None
    assert {it["id"] for it in r["items"]} == {"a", "b", "c"}
