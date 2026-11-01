# -*- coding: utf-8 -*-
"""其余 4 类抽取器轻量测试（FakeSession，沙箱无网仅测纯逻辑）。"""
import os
import tempfile

from xq_pump.core import FakeSession
from xq_pump.cursor import CursorStore
from xq_pump.store import Store
from xq_pump.extractors import hot as ex_hot
from xq_pump.extractors import quotes as ex_quotes
from xq_pump.extractors import kline as ex_kline
from xq_pump.extractors import timeline as ex_timeline


def _env():
    d = tempfile.mkdtemp()
    store = Store(os.path.join(d, "t.db"))
    cursor = CursorStore(os.path.join(d, "c"))
    return store, cursor


def _item(i):
    return {"id": i, "created_at": 1700000000000, "title": "t%d" % i,
            "description": "d%d" % i, "user_id": "u", "target": "",
            "comment_count": 0, "view_count": 1, "retweet_count": 0,
            "like_count": 0, "reward_count": 0, "source": "web",
            "hot_rank": i}


def test_hot_incremental():
    store, cursor = _env()
    pages = [
        {"list": [_item(1), _item(2)], "next_max_id": "100"},
        {"list": [_item(3)], "max_id": "200"},
        {"list": [], "next_max_id": None},
    ]
    sess = FakeSession(pages=pages)
    res = ex_hot.collect_hot(sess, store, cursor, max_pages=10)
    assert res["new"] == 3
    # 第二跑：seen 去重 → 0 新增
    sess2 = FakeSession(pages=pages)
    res2 = ex_hot.collect_hot(sess2, store, cursor, max_pages=10)
    assert res2["new"] == 0
    assert store.count("xq_hot_post") == 3
    store.close()


def test_quotes_parse_and_store():
    store, cursor = _env()
    payload = {"data": {"items": [
        {"symbol": "SH600519", "current": 1500.0, "open": 1490.0,
         "high": 1510.0, "low": 1485.0, "volume": 100, "amount": 1.5e6,
         "pct_chg": 1.2},
    ]}}
    sess = FakeSession(quotes={"SH600519": payload})
    res = ex_quotes.collect_quotes(sess, store, cursor, symbols=["SH600519"])
    assert res["new"] == 1
    assert store.count("xq_quote") == 1
    row = store.con.execute(
        "SELECT close FROM xq_quote WHERE symbol='SH600519'").fetchone()
    assert row[0] == 1500.0
    store.close()


def test_kline_parse_and_store():
    store, cursor = _env()
    payload = {"data": {"column": ["date", "open", "high", "low", "close",
                                    "volume", "amount"],
                        "item": [["2026-01-01", 100, 101, 99, 100.5, 1, 2],
                                 ["2026-01-02", 100.5, 102, 100, 101.5, 1, 2]]}}
    sess = FakeSession(klines={"SH600519": payload})
    res = ex_kline.collect_kline(sess, store, cursor, symbols=["SH600519"],
                                 period="day", count=240)
    assert res["new"] == 2
    assert store.count("xq_kline") == 2
    store.close()


def test_timeline_incremental():
    store, cursor = _env()
    pages = [
        {"statuses": [_item(1), _item(2)], "last_id": "L100"},
        {"statuses": [_item(3)], "last_id": "L200"},
        {"statuses": [], "last_id": None},
    ]
    sess = FakeSession(pages=pages)
    res = ex_timeline.collect_timeline(sess, store, cursor, user_id="u1", max_pages=10)
    assert res["new"] == 3
    assert res["requests"] == 3
    # 断点已存
    st = cursor.load_type("timeline")["u1"]
    assert st["last_id"] == "L200"
    # 复用 xq_status 表，source 标记 user_timeline
    row = store.con.execute(
        "SELECT source FROM xq_status WHERE status_id='1'").fetchone()
    assert row[0] == "user_timeline:u1"
    store.close()
