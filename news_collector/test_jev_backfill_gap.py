# -*- coding: utf-8 -*-
"""P0 RFC 方案② D3 单测：jev_backfill_gap 缺口报表 + 幂等回放 + 错峰守卫。"""
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_backfill_gap as bg
import jev_feed as jf


def _tmp_db(tmp_path):
    tdb = tmp_path / "news.db"
    db = sqlite3.connect(str(tdb))
    db.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
        "title TEXT, content TEXT, hit INTEGER, pushed INTEGER, "
        "digested INTEGER, created_at TEXT, stocks TEXT, jev TEXT)"
    )
    db.execute(
        "CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, "
        "ts TEXT, ok INTEGER, new_count INTEGER, push_count INTEGER, "
        "detail TEXT)"
    )
    db.commit()
    db.close()
    return str(tdb)


def _row(i, day="20261106"):
    return {
        "ts_code": "%06d.SZ" % i,
        "title": "标题%03d" % i,
        "content": "正文%03d" % i,
        "pub_date": "2026-11-06",
        "pub_time": "10:00:00",
        "url": "http://x/%s" % i,
        "_fetched_at": "2026-11-06 10:00:%02d" % (i % 60),
        "_src_rowid": i + 1,
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    jf.DB = _tmp_db(tmp_path)
    # 预置：id=标题000 已入库（同日）
    db = sqlite3.connect(jf.DB)
    db.execute(
        "INSERT INTO news(id, source, ts, title, content, hit, pushed, "
        "digested, created_at, stocks, jev) VALUES (?,?,?,?,?,0,0,0,?,?,NULL)",
        (jf._xd_id("news", "http://x/0", "标题000"), "xd_news",
         "2026-11-06 10:00", "标题000", "", "2026-11-07 09:00", "[]"),
    )
    db.commit()
    db.close()
    yield


def test_forbidden_window_bounds(env):
    assert bg._in_forbidden_window(__import__("datetime").datetime(2026, 11, 7, 22, 0))
    assert bg._in_forbidden_window(__import__("datetime").datetime(2026, 11, 7, 3, 0))
    assert not bg._in_forbidden_window(__import__("datetime").datetime(2026, 11, 7, 12, 0))


def test_report_refuses_in_night_window(env, monkeypatch, capsys):
    monkeypatch.setattr(bg, "_in_forbidden_window", lambda now=None: True)
    rc = bg.main(["--start", "20261106", "--end", "20261106"])
    assert rc == 2
    assert "错峰" in capsys.readouterr().out


def _fake_dm_news_only(n):
    """news 别名返回 n 行；notice 别名返回空表（报表/回放只算 news）。"""
    df = pd.DataFrame([_row(i) for i in range(n)])

    def read(alias, *a, **k):
        return df if alias == "news" else pd.DataFrame()

    return read


def test_gap_report_counts_missing(env, monkeypatch, capsys):
    monkeypatch.setattr(bg, "_in_forbidden_window", lambda now=None: False)
    monkeypatch.setattr(jf.dm, "read", _fake_dm_news_only(3))
    rc = bg.main(["--start", "20261106", "--end", "20261106"])
    assert rc == 0
    out = capsys.readouterr().out
    # 源 3 唯一 id，1 条已入库 → news 缺口 2、notice 0
    assert "缺口合计=2" in out


def test_apply_replays_missing_idempotently(env, monkeypatch, capsys):
    monkeypatch.setattr(bg, "_in_forbidden_window", lambda now=None: False)
    monkeypatch.setattr(jf.dm, "read", _fake_dm_news_only(3))
    rc = bg.main(["--start", "20261106", "--end", "20261106", "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "已回放=2" in out
    db = sqlite3.connect(jf.DB)
    try:
        n = db.execute("SELECT COUNT(*) FROM news WHERE source='xd_news'").fetchone()[0]
    finally:
        db.close()
    assert n == 3
    # 二轮幂等：缺口归零
    rc2 = bg.main(["--start", "20261106", "--end", "20261106", "--apply"])
    assert rc2 == 0
    assert "缺口合计=0" in capsys.readouterr().out


def test_apply_source_failure_aborts(env, monkeypatch, capsys):
    monkeypatch.setattr(bg, "_in_forbidden_window", lambda now=None: False)
    df = pd.DataFrame([_row(i) for i in range(3)])

    def read(alias, *a, **k):
        # 报表阶段（无 after）正常返回；--apply 走 feed_source_v2（带 after）时炸
        if k.get("after") is not None:
            raise RuntimeError("locked")
        return df if alias == "news" else pd.DataFrame()

    monkeypatch.setattr(jf.dm, "read", read)
    rc = bg.main(["--start", "20261106", "--end", "20261106", "--apply"])
    assert rc == 1
    assert "APPLY FAIL" in capsys.readouterr().out
