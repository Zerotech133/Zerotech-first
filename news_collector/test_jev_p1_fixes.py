# -*- coding: utf-8 -*-
"""JEV P1 四项修复回归：schema/动态batch/复核队列/失败计数。"""
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jev_batch  # noqa: E402
import jev_review_queue  # noqa: E402
import jev_schema  # noqa: E402


def _news(conn):
    conn.execute("CREATE TABLE news (id TEXT PRIMARY KEY, ts TEXT, title TEXT, "
                 "stocks TEXT, jev TEXT)")
    conn.commit()


def test_schema_idempotent_and_backup(tmp_path):
    db = tmp_path / "news.db"
    con = sqlite3.connect(str(db))
    _news(con)
    con.close()
    added = jev_schema.ensure_path(db)
    assert set(added) == set(jev_schema.META_COLUMNS)
    assert list((tmp_path / "backups").glob("news.db.*.bak"))
    added2 = jev_schema.ensure_path(db)
    assert added2 == []


def test_dynamic_batch_uses_deadline_and_history(tmp_path, monkeypatch):
    p = tmp_path / "metrics.json"
    monkeypatch.setenv("JEV_BACKFILL_METRICS", str(p))
    # 无历史 → 默认 3s；deadline 1800 应明显小于 600 上限
    n = jev_batch.estimate_batch(1800, 5000, explicit=0)
    assert 8 <= n <= 600
    jev_batch.record_run(600, 500, 4000, 500)
    assert jev_batch.median_item_s() == 1.2
    n2 = jev_batch.estimate_batch(1800, 5000, explicit=0)
    assert n2 >= n
    assert jev_batch.estimate_batch(1800, 5000, explicit=42) == 42


def test_review_queue_low_conf_only():
    con = sqlite3.connect(":memory:")
    _news(con)
    rows = [
        ("a", "2026-10-30 10:00", "low", "[]", json.dumps({"bias": "bull", "conf": 0.4})),
        ("b", "2026-10-30 10:01", "high", "[]", json.dumps({"bias": "bear", "conf": 0.9})),
        ("c", "2026-10-30 10:02", "mid", "[]", json.dumps({"bias": "neutral", "conf": 0.69})),
    ]
    con.executemany("INSERT INTO news VALUES (?,?,?,?,?)", rows)
    con.commit()
    r = jev_review_queue.build(con, limit=100, threshold=0.7)
    assert r["added"] == 2
    s = jev_review_queue.status(con)
    assert s["pending"] == 2
    assert jev_review_queue.resolve(con, "a", "correct", "tester")
    assert jev_review_queue.status(con)["pending"] == 1
    con.close()


def test_judge_failures_are_reported(monkeypatch):
    import jev_backfill
    calls = {"n": 0}

    def fake(title, stocks):
        calls["n"] += 1
        raise RuntimeError("model down")

    monkeypatch.setattr(jev_backfill.news_ai, "jev_judge_one", fake)
    failures = {}
    out = jev_backfill._judge_batch_sleep(
        [{"id": "x", "title": "t", "stocks": []}], 0, failures=failures)
    assert out == {}
    assert "model down" in failures["x"]
    assert calls["n"] == 2  # 与既有失败重试一次一致
