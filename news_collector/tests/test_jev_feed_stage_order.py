# -*- coding: utf-8 -*-
"""B 案契约（2026-11-13 老田裁定）：采集全前置、判读在后、采集即固化水位。

原实现是「采 news → 立刻判 news → 采 notice → 判 notice」。判读是小时级的
（单并发 ollama），news 一顿判读就能把计划任务的 PT3H 吃满，notice 连抓都
没抓到 —— 11-12 实证：21:00:11 落完 news 500 条后再无输出，00:00 被强杀，
notice 0 条入库。

本组用例锁三件事：
1. 顺序：两个源都 feed 完，才允许出现第一次 judge；
2. 采集即固化：第一次 judge 被调用时，runs 里必须已有 ok=1 且水位已推进的
   记录 —— 否则判读阶段被强杀时水位随收尾一起丢，下轮重拉同一批；
3. 不增行：正常完成仍是每轮一行 runs（采集写、收尾更新同一行）。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jev_feed as jf


def _day():
    """'20261113'，按码点构造（防输出/传输通道串味，见用户级铁律）。"""
    return "".join(chr(c) for c in (0x32, 0x30, 0x32, 0x36, 0x31, 0x31, 0x31, 0x33))


def _tmp_db(tmp_path):
    tdb = tmp_path / "news.db"
    db = sqlite3.connect(str(tdb))
    db.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
        "title TEXT, content TEXT, hit INTEGER, pushed INTEGER, "
        "digested INTEGER, created_at TEXT, stocks TEXT, jev TEXT, tag_conf TEXT)"
    )
    db.execute(
        "CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, "
        "ts TEXT, ok INTEGER, new_count INTEGER, push_count INTEGER, "
        "detail TEXT)"
    )
    db.commit()
    db.close()
    return str(tdb)


@pytest.fixture
def env(tmp_path, monkeypatch):
    jf.DB = _tmp_db(tmp_path)
    monkeypatch.delenv(jf.WM_V2_ENV, raising=False)
    yield


def _jr(judged=0):
    return {"judged": judged, "missing": 0, "error": None, "pref_skipped": 0}


def _arm(monkeypatch, seq, fail=(), judge_raise=None):
    def fake_fs(alias, kind, wm_date, days, cap, dryrun=False):
        seq.append("feed:%s" % kind)
        if dryrun:
            return 0, 0, None, None
        if kind in fail:
            return 0, 0, None, "源不可用"
        return 3, 3, _day(), None

    def fake_jp(kind, cap, dryrun=False):
        seq.append("judge:%s" % kind)
        if judge_raise is not None:
            raise judge_raise
        return _jr(1)

    monkeypatch.setattr(jf, "feed_source", fake_fs)
    monkeypatch.setattr(jf, "judge_pending", fake_jp)


def _last_run():
    db = sqlite3.connect(jf.DB)
    try:
        row = db.execute(
            "SELECT id, ok, detail FROM runs WHERE kind=? ORDER BY id DESC LIMIT 1",
            (jf.FEED_KIND,),
        ).fetchone()
        n = db.execute(
            "SELECT COUNT(*) FROM runs WHERE kind=?", (jf.FEED_KIND,)
        ).fetchone()[0]
    finally:
        db.close()
    return row, n


def test_both_sources_fetched_before_any_judge(env, monkeypatch):
    """核心钉子：两个源全采完，才允许第一次判读。"""
    seq = []
    _arm(monkeypatch, seq)
    assert jf.main([]) == 0
    assert seq == ["feed:news", "feed:notice", "judge:news", "judge:notice"]


def test_watermark_persisted_before_first_judge(env, monkeypatch):
    """采集结束即落库：第一次 judge 时 runs 已有 ok=1 且 wm 已推进。"""
    seen = {}

    def fake_fs(alias, kind, wm_date, days, cap, dryrun=False):
        return 3, 3, _day(), None

    def fake_jp(kind, cap, dryrun=False):
        if "row" not in seen:
            seen["row"], _ = _last_run()
        return _jr(0)

    monkeypatch.setattr(jf, "feed_source", fake_fs)
    monkeypatch.setattr(jf, "judge_pending", fake_jp)
    assert jf.main([]) == 0

    assert seen["row"] is not None, "判读已开始，runs 里却没有任何记录（水位未固化）"
    _id, ok, detail = seen["row"]
    assert ok == 1
    d = json.loads(detail)
    assert d["phase"] == "feed"
    assert d["wm"]["news"] == _day() and d["wm"]["notice"] == _day()


def test_watermark_survives_judge_crash(env, monkeypatch):
    """判读阶段被强杀（这里用 KeyboardInterrupt 模拟），水位必须还在。"""
    _arm(monkeypatch, [], judge_raise=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        jf.main([])

    row, n = _last_run()
    assert row is not None, "判读崩溃后 runs 无记录 —— 水位丢了，下轮会重拉同一批"
    _id, ok, detail = row
    assert ok == 1 and n == 1
    d = json.loads(detail)
    assert d["phase"] == "feed"
    assert d["wm"]["notice"] == _day()


def test_judge_still_runs_when_feed_fails(env, monkeypatch):
    """采集失败不阻断判读（保持改动前的行为）。"""
    seq = []
    _arm(monkeypatch, seq, fail={"notice"})
    assert jf.main([]) == 1  # errors 非空 → ok=0 → rc 1
    assert seq == ["feed:news", "feed:notice", "judge:news", "judge:notice"]


def test_feed_error_keeps_run_ok_zero(env, monkeypatch):
    """源失败 → ok=0 → load_watermark 读不到，水位不推进（fail-visible）。"""
    _arm(monkeypatch, [], fail={"news"})
    assert jf.main([]) == 1
    _id, ok, _detail = _last_run()[0]
    assert ok == 0
    assert jf.load_watermark() == {}


def test_one_runs_row_per_round(env, monkeypatch):
    """正常完成只增一行：采集插入、收尾更新同一行，不写两条。"""
    _arm(monkeypatch, [])
    assert jf.main([]) == 0
    row, n = _last_run()
    assert n == 1
    _id, ok, detail = row
    assert ok == 1
    d = json.loads(detail)
    assert d["phase"] == "done"
    assert d["src"]["news"]["inserted"] == 3


def test_dryrun_writes_no_runs(env, monkeypatch):
    """dryrun 一律不落库（采集与判读都不碰 runs）。"""
    _arm(monkeypatch, [])
    assert jf.main(["--dryrun"]) == 0
    assert _last_run()[1] == 0
