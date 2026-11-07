# -*- coding: utf-8 -*-
"""api_jev_platform 单测：聚合结构钉子 + 只读纪律 + 分派路由。"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_platform as ap


@pytest.fixture()
def env(tmp_path, monkeypatch):
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
    detail = json.dumps({
        "wm": {"news": {"v": 2, "date": "20261106", "cur": None}},
        "src": {"news": {"inserted": 150, "fetched": 500, "cap_hit": True,
                          "pref_skipped": 3}},
        "errors": [],
    })
    con.execute(
        "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
        "VALUES ('jev_feed','2026-11-06 23:49',1,150,0,?)", (detail,))
    con.execute(
        "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
        "VALUES ('ai','2026-11-07 08:01',1,0,0,'markets=66')")
    con.execute(
        "INSERT INTO news(id, source, ts, title, content, hit, pushed, "
        "digested, created_at, stocks, jev) VALUES "
        "('a','sina','2026-11-07 08:00','t','c',0,0,0,'2026-11-07 08:00','[]',NULL),"
        "('b','xd_notice','2026-11-06 10:00','t2','c',0,0,0,'2026-11-06 10:00','[]',"
        "'{\"bias\":\"neutral\",\"src\":\"prefilter\"}')")
    con.commit()
    con.close()
    monkeypatch.setattr(ap, "NEWS_DB", str(tdb))
    # 哨兵指向同一临时库；夜批日志缺失（available False / 违规均可）
    import jev_sentinel
    monkeypatch.setattr(jev_sentinel, "NEWS_DB", tdb)
    monkeypatch.setattr(jev_sentinel, "NIGHT_LOG", tmp_path / "nope.log")
    yield


def test_compute_schema_and_blocks(env):
    d = ap.compute(7, fresh=True)
    assert d["schema"] == "jev_platform.v1"
    assert d["feed"]["latest_ok"]["ts"] == "2026-11-06 23:49"
    assert d["feed"]["latest_ok"]["wm"]["news"]["date"] == "20261106"
    # src 保留白名单键（cap_hit 保留观测）
    src = d["feed"]["recent"][0]["src"]["news"]
    assert src["cap_hit"] is True and src["pref_skipped"] == 3
    assert d["queue"]["total"] == 1
    tp = d["throughput"][-1]
    assert tp["ingested"] == 1 and tp["prefilter_skipped"] == 1
    assert any(t["kind"] == "ai" for t in d["timeline"])


def test_readonly_discipline(env, monkeypatch):
    """钉子：compute 全程 news.db 连接必须 mode=ro（拦截 sqlite3.connect）。"""
    calls = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        calls.append((target, dict(k)))
        return real(target, *a, **k)

    monkeypatch.setattr(ap.sqlite3, "connect", spy)
    ap.compute(7, fresh=True)
    for target, kw in calls:
        if str(target).endswith("news.db") or "news.db" in str(target):
            assert kw.get("uri") and "mode=ro" in str(target), (target, kw)


def test_cache_ttl_and_fresh(env):
    d1 = ap.compute(7, fresh=True)
    d2 = ap.compute(7)  # 命中缓存（同对象）
    assert d2 is d1
    d3 = ap.compute(7, fresh=True)
    assert d3 is not d1


class _H:
    def __init__(self):
        self.status = None
        self.body = None

    def _json(self, obj, status=200):
        self.status, self.body = status, obj


def test_handle_ok_and_bad_params(env):
    h = _H()
    ap.handle_get(h, "/api/jev/platform", "days=7")
    assert h.status == 200 and h.body["schema"] == "jev_platform.v1"
    h2 = _H()
    ap.handle_get(h2, "/api/jev/platform", "days=99")
    assert h2.status == 400 and h2.body["error"] == "invalid_params"
    h3 = _H()
    ap.handle_get(h3, "/api/jev/platform", "days=abc")
    assert h3.status == 400


def test_night_block_degrades(env):
    d = ap.compute(7, fresh=True)
    # 日志缺失：check_night 正常返回违规（可评估），非崩溃
    assert d["night"]["available"] is True
    assert d["night"]["ok"] is False and "不存在" in (d["night"]["reason"] or "")
    assert d["sentinel"]["ok"] is False  # 夜查违规如实呈现
