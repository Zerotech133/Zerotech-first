# -*- coding: utf-8 -*-

import pytest
pytest.importorskip("jieba")
# ^ 可选依赖守护：缺 jieba 时跳过而非收集报错（2026-10-28）
"""tests/test_xq_word_tempo.py — 词频温度计（word_tempo）纯逻辑单测

覆盖：title_words 复用不变、ratio 平滑（base_avg=0 场景）、
today_n<2 或 ratio<2 过滤、窗口 SQL 日期边界（day-6 ~ day-1 不含当日）、
ad/edu 与 parse_ok=0 排除、当日 Counter 复用不重扫。
"""

import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xq_daily_report as rep

SCHEMA = (
    'CREATE TABLE xq_status (status_id INTEGER PRIMARY KEY, created_at TEXT,'
    ' fetched_at TEXT, title TEXT, "desc" TEXT, category TEXT);'
    'CREATE TABLE xq_status_classified (status_id INTEGER, event_type TEXT,'
    ' sentiment TEXT, tickers TEXT, confidence REAL, parse_ok INTEGER,'
    ' model TEXT, prompt_version TEXT, content_type TEXT);'
)

_seq = [0]


def _mkdb(rows):
    """rows: [(created_at, title, sentiment, content_type, parse_ok)] → 内存库"""
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for created_at, title, sn, ct, pok in rows:
        _seq[0] += 1
        sid = _seq[0]
        con.execute("INSERT INTO xq_status VALUES (?,?,?,?,?,?)",
                    (sid, created_at, created_at, title, "", "stock"))
        con.execute("INSERT INTO xq_status_classified VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, "行情观点", sn, "[]", 0.9, pok, "m", "v1", ct))
    con.commit()
    return con


def _c(words=None):
    from collections import Counter
    return Counter(words or {})


def test_title_words_reuse_unchanged():
    """title_words 行为不变：停用词/单字/纯数字过滤，帖内去重"""
    ws = rep.title_words("这个如何上涨？利好出尽，上涨 123")
    assert "上涨" in ws and "利好" in ws
    assert "这个" not in ws and "如何" not in ws
    assert all(len(w) >= 2 for w in ws)
    assert not any(re.fullmatch(r"[\dA-Za-z%.:：,，！!？?]+", w) for w in ws)
    # 帖内去重：重复词只出现一次（set 语义）
    assert len(rep.title_words("上涨上涨")) <= len(set(rep.title_words("上涨上涨")))


def test_ratio_smooth_base_zero():
    """base_avg=0 新词：ratio = today_n / 0.5（平滑），today_n>=2 且 ratio>=2 才保留"""
    day = "2020-05-13"
    con = _mkdb([
        # 前 6 日窗口（05-07 ~ 05-12）无该词 → 基线 0
        ("2020-05-12 10:00:00", "大盘震荡", "bearish", "community", 1),
        # 当日两条「利好出尽」→ today_n=2, base_avg=0 → ratio=4
        ("2020-05-13 09:00:00", "利好出尽", "bearish", "community", 1),
        ("2020-05-13 10:00:00", "利好出尽", "bearish", "community", 1),
    ])
    today = {"bearish": _c({"利好出尽": 2}), "bullish": _c()}
    out = rep.word_tempo(con, day, today_words=today)
    hit = [r for r in out if r[1] == "利好出尽"]
    assert len(hit) == 1
    sn, w, n, b, ratio = hit[0]
    assert sn == "bearish" and n == 2
    assert b == 0.0
    assert ratio == 4.0   # 2 / max(0, 0.5)


def test_filter_low_n_and_low_ratio():
    """today_n<2 或 ratio<2 的一律过滤"""
    day = "2020-05-13"
    # 基线：8 帖含「减持」（帖内去重后 base=8，base_avg=8/6≈1.33）
    # → 当日 2 次 ratio=2/1.33=1.5 <2 被滤
    rows = []
    for d in ("2020-05-07", "2020-05-08", "2020-05-09", "2020-05-10",
              "2020-05-11", "2020-05-12"):
        rows.append((d + " 10:00:00", "减持", "bearish", "community", 1))
    for d in ("2020-05-09", "2020-05-11"):
        rows.append((d + " 15:00:00", "减持", "bearish", "community", 1))
    con = _mkdb(rows)
    today = {"bearish": _c({"减持": 2,          # ratio=1.5 <2 → 滤
                            "独角词": 1,        # today_n<2 → 滤
                            "回升": 3}),        # 新词 base 0 → ratio=6 → 留
             "bullish": _c()}
    out = rep.word_tempo(con, day, today_words=today)
    words = [w for _sn, w, _n, _b, _r in out]
    assert words == ["回升"]


def test_window_date_bounds_excludes_today():
    """窗口边界：day-6 ~ day-1 计入基线，day-7 与当日不计入"""
    day = "2020-05-13"
    con = _mkdb([
        ("2020-05-06 23:00:00", "减持", "bearish", "community", 1),   # day-7 窗口外
        ("2020-05-07 00:00:00", "减持", "bearish", "community", 1),   # day-6 窗口首日
        ("2020-05-12 23:59:59", "减持", "bearish", "community", 1),   # day-1 窗口末日
        ("2020-05-13 00:00:00", "减持", "bearish", "community", 1),   # 当日零点不入基线
    ])
    # 用 Counter 复用路径，直接核对基线：base_avg = 2/6
    today = {"bearish": _c({"减持": 2}), "bullish": _c()}
    out = rep.word_tempo(con, day, today_words=today)
    # base_avg=2/6≈0.333 → ratio = 2/0.5 = 4（平滑生效）
    hit = [r for r in out if r[1] == "减持"]
    assert len(hit) == 1 and abs(hit[0][3] - 2.0 / 6.0) < 1e-9 and hit[0][4] == 4.0


def test_ad_edu_and_parse_bad_excluded():
    """ad/edu 行与 parse_ok=0 行不进基线，也不进现算当日 Counter"""
    day = "2020-05-13"
    con = _mkdb([
        # 基线里全是被排除的行 → 「减持」基线应为 0
        ("2020-05-10 10:00:00", "减持", "bearish", "ad", 1),
        ("2020-05-11 10:00:00", "减持", "bearish", "edu", 1),
        ("2020-05-12 10:00:00", "减持", "bearish", "community", 0),  # parse_ok=0
    ])
    today = {"bearish": _c({"减持": 3}), "bullish": _c()}
    out = rep.word_tempo(con, day, today_words=today)
    hit = [r for r in out if r[1] == "减持"]
    assert len(hit) == 1 and hit[0][3] == 0.0 and hit[0][4] == 6.0


def test_today_counter_reuse_no_rescan():
    """传入当日 Counter 时复用不重扫：库里当日无行也照样出结果"""
    day = "2020-05-13"
    con = _mkdb([])   # 全库为空
    today = {"bearish": _c({"黑天鹅": 2}), "bullish": _c()}
    out = rep.word_tempo(con, day, today_words=today)
    assert [(r[0], r[1], r[2]) for r in out] == [("bearish", "黑天鹅", 2)]


def test_today_words_fallback_scans_db():
    """不传 today_words 时由窗口查询现算（含当日行）"""
    day = "2020-05-13"
    con = _mkdb([
        ("2020-05-13 09:00:00", "减持", "bearish", "community", 1),
        ("2020-05-13 10:00:00", "减持", "bearish", "community", 1),
        ("2020-05-13 11:00:00", "无关", "bullish", "community", 1),
    ])
    out = rep.word_tempo(con, day, today_words=None)
    assert ("bearish", "减持", 2, 0.0, 4.0) in out
    assert all(r[1] != "无关" for r in out)   # today_n=1 被滤


def test_topn_cap_per_side():
    """每边最多 topn 条，按 ratio 降序"""
    day = "2020-05-13"
    today = {"bearish": _c({("词%d" % i): 3 for i in range(12)}), "bullish": _c()}
    con = _mkdb([])
    out = rep.word_tempo(con, day, today_words=today, topn=8)
    bear = [r for r in out if r[0] == "bearish"]
    assert len(bear) == 8
    ratios = [r[4] for r in bear]
    assert ratios == sorted(ratios, reverse=True)
