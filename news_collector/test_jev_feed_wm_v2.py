# -*- coding: utf-8 -*-
"""P0 RFC 方案②单测：jev_feed v2 连续水位 + 稳定游标（JEV_WM_V2 门控）。

钉子：
- env 缺省关闭 → v1 路径逐字保持（cap_hit 信号仍在）；
- JEV_WM_V2=1 → feed_source_v2 分页耗尽推进（cur=None）、安全阀续走
  （cur=游标，page_limit 留痕）、源失败不写水位；
- v1 路径对 v2 dict 水位降级读 .get("date")（RFC §7 回滚口径）；
- v2 下「拉满 cap」不再触发 cap_hit 截断告警（分页是正常语义）。
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_feed as jf


# ── 脚手架 ────────────────────────────────────────────────────────────
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


def _row(i, day="20261106", sub=None, rid=None):
    return {
        "ts_code": "%06d.SZ" % i,
        "symbol": "%06d" % i,
        "title": "标题%03d" % i,
        "content": "正文%03d" % i,
        "pub_date": "2026-11-06" if day == "20261106" else day,
        "pub_time": "10:00:00",
        "url": "http://x/%s" % i,
        "_fetched_at": sub or ("2026-11-06 10:00:%02d" % (i % 60)),
        "_src_rowid": rid if rid is not None else i + 1,
    }


class _FakeDm:
    """按 (date, sub, rid) 三键模拟 SQL 游标分页的假 dm。"""

    def __init__(self, rows):
        self.rows = sorted(
            rows,
            key=lambda r: (r["pub_date"].replace("-", ""),
                           r["_fetched_at"] or "", r["_src_rowid"]),
        )
        self.calls = []

    def read(self, alias, start=None, limit=None, after=None, **kw):
        self.calls.append({"alias": alias, "start": start,
                           "limit": limit, "after": after})
        date = str(after.get("date")) if after else str(
            (pd.Timestamp(start) - pd.Timedelta(days=1)).strftime("%Y%m%d"))
        sub = str(after.get("sub") or "") if after else ""
        rid = int(after.get("rid") or 0) if after else 0
        kept = [
            r for r in self.rows
            if (r["pub_date"].replace("-", "") > date)
            or (r["pub_date"].replace("-", "") == date
                and (r["_fetched_at"] or "") > sub)
            or (r["pub_date"].replace("-", "") == date
                and (r["_fetched_at"] or "") == sub
                and r["_src_rowid"] > rid)
        ]
        kept = kept[: limit or 200]
        return pd.DataFrame(kept) if kept else pd.DataFrame()


@pytest.fixture()
def feed_env(tmp_path, monkeypatch):
    jf.DB = _tmp_db(tmp_path)
    monkeypatch.delenv(jf.WM_V2_ENV, raising=False)
    yield
    # 还原由各测试自行 monkeypatch dm / news_ai


# ── _wm_norm ──────────────────────────────────────────────────────────
def test_wm_norm_accepts_v1_v2_rejects_garbage(feed_env):
    assert jf._wm_norm("20261106") == {"v": 1, "date": "20261106", "cur": None}
    d = {"v": 2, "date": "20261106", "cur": {"date": "20261106", "sub": "x", "rid": 3}}
    assert jf._wm_norm(d) == d
    assert jf._wm_norm(None) is None
    assert jf._wm_norm("abc") is None
    assert jf._wm_norm({"date": ""}) is None


# ── feed_source_v2 ────────────────────────────────────────────────────
def test_v2_drains_day_and_advances_exhausted(feed_env, monkeypatch):
    fake = _FakeDm([_row(i) for i in range(7)])
    monkeypatch.setattr(jf, "dm", fake)
    n_ins, n_fetched, wm, err = jf.feed_source_v2(
        "news", "news", "20261106", 2, 3)  # cap=3 → 页 3/3/1
    assert err is None
    assert (n_ins, n_fetched) == (7, 7)
    assert wm["v"] == 2 and wm["date"] == "20261106" and wm["cur"] is None
    assert len(fake.calls) == 3  # 3/3/1 → 耗尽停


def test_v2_rerun_idempotent(feed_env, monkeypatch):
    fake = _FakeDm([_row(i) for i in range(5)])
    monkeypatch.setattr(jf, "dm", fake)
    _, _, wm, _ = jf.feed_source_v2("news", "news", "20261106", 2, 2)
    n_ins2, n_fetched2, wm2, err2 = jf.feed_source_v2(
        "news", "news", wm, 2, 2)
    assert err2 is None and n_ins2 == 0 and wm2["cur"] is None
    assert n_fetched2 == 5  # 耗尽日整日重读（幂等重叠语义）


def test_v2_page_limit_keeps_cursor(feed_env, monkeypatch):
    """源恒满页：安全阀截停，游标随水位保存（cur 非 None，续走不丢数）。"""
    fake = _FakeDm([_row(i) for i in range(500)])  # cap=2 → 永不耗尽
    monkeypatch.setattr(jf, "dm", fake)
    n_ins, n_fetched, wm, err = jf.feed_source_v2("news", "news", "20261106", 2, 2)
    assert err is None
    assert n_fetched == jf.WM_V2_MAX_PAGES * 2  # 80 行
    assert n_ins == 80
    assert wm["cur"] is not None  # 游标保存，下轮续走
    # 续走轮从游标继续，不重不漏
    n_ins2, n_fetched2, wm2, err2 = jf.feed_source_v2(
        "news", "news", wm, 2, 2)
    assert err2 is None and n_ins2 == 80
    db = sqlite3.connect(jf.DB)
    try:
        total = db.execute(
            "SELECT COUNT(*) FROM news WHERE source='xd_news'").fetchone()[0]
    finally:
        db.close()
    assert total == 160  # 两轮各排 80，零重复


def test_v2_source_failure_no_watermark(feed_env, monkeypatch):
    class BoomDm:
        def read(self, *a, **k):
            raise RuntimeError("db locked")

    monkeypatch.setattr(jf, "dm", BoomDm())
    n_ins, n_fetched, wm, err = jf.feed_source_v2("news", "news", "20261106", 2, 3)
    assert err and "异常" in err and wm is None and n_ins == 0


def test_v2_dryrun_no_write(feed_env, monkeypatch, capsys):
    fake = _FakeDm([_row(i) for i in range(4)])
    monkeypatch.setattr(jf, "dm", fake)
    n_ins, n_fetched, wm, err = jf.feed_source_v2(
        "news", "news", "20261106", 2, 2, dryrun=True)
    assert (n_ins, n_fetched, wm, err) == (0, 0, None, None)
    assert "wm_v2" in capsys.readouterr().out


# ── main() 门控 ───────────────────────────────────────────────────────
def _seed_v2_wm(db_path):
    """预置一条 v2 dict 水位的 runs 记录。"""
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
        "VALUES (?,?,1,0,0,?)",
        (jf.FEED_KIND, "2026-11-06 09:00",
         json.dumps({"wm": {"news": {"v": 2, "date": "20261106", "cur": None},
                            "notice": {"v": 2, "date": "20261106", "cur": None}}},
                    ensure_ascii=False)),
    )
    db.commit()
    db.close()


def test_main_env_off_uses_v1_and_degrades_dict_wm(feed_env, monkeypatch, capsys):
    _seed_v2_wm(jf.DB)
    captured = {}

    def fake_fs(alias, kind, wm_date, days, cap, dryrun=False):
        captured[kind] = wm_date
        return 0, 0, None, None

    monkeypatch.setattr(jf, "feed_source", fake_fs)
    rc = jf.main(["--dryrun"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wm_v2=False" in out  # 缺省 v1
    # v1 对 dict 水位降级读 date（RFC §7 回滚口径）
    assert captured == {"news": "20261106", "notice": "20261106"}


def _jr():
    return {"judged": 0, "missing": 0, "error": None, "pref_skipped": 0}


def test_main_env_on_uses_v2_and_writes_v2_wm(feed_env, monkeypatch, capsys):
    fake = _FakeDm([_row(i) for i in range(4)])
    monkeypatch.setattr(jf, "dm", fake)
    monkeypatch.setattr(jf, "judge_pending", lambda *a, **k: _jr())
    monkeypatch.setenv(jf.WM_V2_ENV, "1")
    rc = jf.main([])
    assert rc == 0
    db = sqlite3.connect(jf.DB)
    try:
        row = db.execute(
            "SELECT detail FROM runs WHERE kind=? ORDER BY id DESC LIMIT 1",
            (jf.FEED_KIND,),
        ).fetchone()
    finally:
        db.close()
    detail = json.loads(row[0])
    wm = detail["wm"]["news"]
    assert wm["v"] == 2 and wm["cur"] is None
    assert "cap_hit" not in detail["src"]["news"]  # 分页非截断，cap_hit 退役


def test_v2_full_page_does_not_trigger_cap_hit(feed_env, monkeypatch):
    """v1 的 cap_hit 语义在 v2 关闭：拉满 cap 是正常分页。"""
    fake = _FakeDm([_row(i) for i in range(6)])
    monkeypatch.setattr(jf, "dm", fake)
    monkeypatch.setattr(jf, "judge_pending", lambda *a, **k: _jr())
    sent = []
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: sent.append(m))
    monkeypatch.setenv(jf.WM_V2_ENV, "1")
    assert jf.main([]) == 0
    assert sent == []
