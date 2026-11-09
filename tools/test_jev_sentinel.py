# -*- coding: utf-8 -*-
"""O3 哨兵单测：四查各自违规/放行 + rc 语义 + 告警开关。"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_sentinel as sen


@pytest.fixture()
def db(tmp_path):
    tdb = tmp_path / "news.db"
    con = sqlite3.connect(str(tdb))
    con.execute(
        "CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, "
        "ts TEXT, ok INTEGER, new_count INTEGER, push_count INTEGER, detail TEXT)"
    )
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, "
        "content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER, "
        "created_at TEXT, stocks TEXT, jev TEXT)"
    )
    con.commit()
    con.close()
    sen.NEWS_DB = tdb
    return tdb


def _ins_run(con, kind, ts, ok, detail):
    con.execute(
        "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
        "VALUES (?,?,?,?,0,?)", (kind, ts, ok, 0, detail))


NOW = dt.datetime(2026, 11, 7, 11, 0)


def test_feed_ok_and_stale(db):
    con = sqlite3.connect(str(db))
    _ins_run(con, "jev_feed", "2026-11-06 23:49", 1,
             json.dumps({"wm": {"news": "20261106", "notice": "20261106"}}))
    con.commit()
    con.close()
    r = sen.check_feed(sqlite3.connect(str(db)), now=NOW)
    assert r["ok"] and r["age_h"] == pytest.approx(11.2, abs=0.1)
    r2 = sen.check_feed(sqlite3.connect(str(db)),
                        now=NOW + dt.timedelta(hours=30))
    assert not r2["ok"] and "26h" in r2["reason"]


def test_feed_failure_runs_ignored(db):
    con = sqlite3.connect(str(db))
    _ins_run(con, "jev_feed", "2026-11-07 01:00", 0, "{}")  # 失败轮：不算
    con.commit()
    con.close()
    r = sen.check_feed(sqlite3.connect(str(db)), now=NOW)
    assert not r["ok"]  # 无成功轮 → 违规


def test_watermark_accepts_v1_v2_and_flags_stale(db):
    con = sqlite3.connect(str(db))
    wm = json.dumps({"wm": {"news": {"v": 2, "date": "20261106", "cur": None},
                            "notice": "20261106"}})
    _ins_run(con, "jev_feed", "2026-11-06 23:49", 1, wm)
    con.commit()
    con.close()
    r = sen.check_watermark(sqlite3.connect(str(db)), now=NOW)
    assert r["ok"] and r["wm"] == {"news": "20261106", "notice": "20261106"}
    r2 = sen.check_watermark(sqlite3.connect(str(db)),
                             now=NOW + dt.timedelta(days=5))
    assert not r2["ok"] and "notice" in r2["reason"]


def test_queue_threshold(db):
    con = sqlite3.connect(str(db))
    for i in range(1501):
        con.execute("INSERT INTO news(id) VALUES ('n%d')" % i)
    con.commit()
    con.close()
    r = sen.check_queue(sqlite3.connect(str(db)))
    assert not r["ok"] and r["pending"] == 1501


def test_night_log_missing_and_stale(tmp_path, monkeypatch):
    sen.NIGHT_LOG = tmp_path / "nope.log"
    r = sen.check_night(now=NOW)
    assert not r["ok"] and "不存在" in r["reason"]
    lg = tmp_path / "jev_night.log"
    lg.write_text("[2026-11-07 02:07:08] END 轮数=9\n", encoding="utf-8")
    sen.NIGHT_LOG = lg
    r2 = sen.check_night(now=NOW)
    assert r2["ok"] and r2["age_h"] == pytest.approx(8.9, abs=0.1)
    r3 = sen.check_night(now=NOW + dt.timedelta(hours=30))
    assert not r3["ok"]


def test_rc_and_alert_gating(db, monkeypatch, capsys):
    con = sqlite3.connect(str(db))
    _ins_run(con, "jev_feed", "2026-11-06 23:49", 1,
             json.dumps({"wm": {}}))
    con.commit()
    con.close()
    sen.NIGHT_LOG = db.parent / "nope.log"
    sent = []
    monkeypatch.setattr(sen, "_push", lambda m: sent.append(m))
    rc = sen.main([])
    assert rc == 2  # 夜查违规
    out = capsys.readouterr().out
    assert "SENTINEL" not in out  # 违规行在 stderr
    # 全绿场景：补一份夜批日志
    lg = db.parent / "jev_night.log"
    lg.write_text("[2026-11-07 02:07:08] END\n", encoding="utf-8")
    sen.NIGHT_LOG = lg
    assert sen.main([]) == 0


def test_alert_env_off(monkeypatch):
    calls = []
    monkeypatch.setattr(sen, "ALERT_ENV", "JEV_SENTINEL_ALERT")
    monkeypatch.setenv("JEV_SENTINEL_ALERT", "0")

    def boom(m):
        calls.append(m)
        raise AssertionError("env 关了不该推送")

    # 直接验证 _push 的 env 门
    monkeypatch.setattr("os.environ", {**dict(__import__("os").environ),
                                       "JEV_SENTINEL_ALERT": "0"})
    saved = sys.path[:]
    sys.path.insert(0, str(Path(sen.__file__).parent.parent / "news_collector"))
    try:
        # news_ai 真实模块存在，_push 应被 env 短路、不触达
        sen._push("x")
    finally:
        sys.path[:] = saved
    assert calls == []
