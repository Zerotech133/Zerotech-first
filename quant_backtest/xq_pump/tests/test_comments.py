# -*- coding: utf-8 -*-
"""comments（个股讨论帖）抽取器测试：翻页/去重/首错/全旧收手/改键/CLI。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xq_pump.core import FakeSession
from xq_pump.cursor import CursorStore
from xq_pump.store import Store
from xq_pump.extractors.comments import collect_comments


def _item(i, created=1700000000000):
    return {"id": i, "created_at": created, "title": "t%d" % i,
            "description": "d%d" % i, "user_id": "u", "target": "",
            "comment_count": 0, "view_count": 1, "retweet_count": 0,
            "like_count": 0, "reward_count": 0, "source": "web"}


def _pages(*pairs):
    return [{"items": lst, "list": None} if lst else {"items": [], "list": None}
            for lst, in pairs]


def _env():
    d = tempfile.mkdtemp()
    store = Store(os.path.join(d, "t.db"))
    cursor = CursorStore(os.path.join(d, "cursors"))
    return store, cursor, d


def test_first_page_new_and_key_rename():
    """首页新帖落库且主键改 comment_id（表内无 status_id 列）。"""
    store, cursor, _ = _env()
    pages = [{"items": [_item(1), _item(2)]},
             {"items": []}]
    sess = FakeSession(pages=pages)
    res = collect_comments(sess, store, cursor, symbols=["SH600519"], max_pages=2)
    assert res["new"] == 2
    assert store.count("xq_comment") == 2
    cols = [r[1] for r in store.con.execute("PRAGMA table_info(xq_comment)")]
    assert "status_id" not in cols and "comment_id" in cols
    row = store.con.execute(
        "SELECT comment_id, symbol FROM xq_comment").fetchone()
    assert row[0] == "1" and row[1] == "SH600519"
    store.close()


def test_dedup_stops_early_on_all_seen():
    """全旧帖提前收手：不翻到第 2 页。"""
    store, cursor, _ = _env()
    store.upsert_comments([{  # 预置一条已见
        "comment_id": "1", "symbol": "SH600519"}])
    pages = [{"items": [_item(1)]},          # 全旧
             {"items": [_item(9)]}]          # 不该被翻到
    sess = FakeSession(pages=pages)
    res = collect_comments(sess, store, cursor, symbols=["SH600519"], max_pages=3)
    assert res["new"] == 0
    assert len(sess.calls) == 1  # 全旧页后提前收手
    store.close()


def test_first_page_failure_is_error_signal():
    """首页失败=错误信号：记 failed、零落库、不误判为空。"""
    store, cursor, _ = _env()
    sess = FakeSession(pages=[None])          # get_json 返回 None
    res = collect_comments(sess, store, cursor, symbols=["SH600519"], max_pages=2)
    assert res["per_symbol"]["SH600519"]["failed"] is True
    assert res["new"] == 0
    store.close()


def test_empty_first_page_marks_empty():
    store, cursor, _ = _env()
    sess = FakeSession(pages=[{"items": []}])
    res = collect_comments(sess, store, cursor, symbols=["SH600519"], max_pages=2)
    assert res["per_symbol"]["SH600519"]["empty"] is True
    assert res["new"] == 0
    store.close()


def test_pagination_until_no_more():
    """翻页到空页自然停止，两页新帖全部落库。"""
    store, cursor, _ = _env()
    pages = [{"items": [_item(1), _item(2)]},
             {"items": [_item(3)]},
             {"items": []}]
    sess = FakeSession(pages=pages)
    res = collect_comments(sess, store, cursor, symbols=["SH600519"], max_pages=5)
    assert res["new"] == 3
    assert store.count("xq_comment") == 3
    store.close()


def test_max_new_cap():
    """max_new 全局封顶生效。"""
    store, cursor, _ = _env()
    pages = [{"items": [_item(i) for i in range(1, 6)]},
             {"items": [_item(i) for i in range(6, 11)]}]
    sess = FakeSession(pages=pages)
    res = collect_comments(sess, store, cursor, symbols=["SH600519"],
                           max_pages=3, max_new=7)
    assert res["new"] == 7
    store.close()


def test_multiple_symbols_independent():
    """多符号各自翻页互不干扰；seen 为 (symbol, comment_id) 复合键。
    注：xq_comment 表主键为单列 comment_id（对齐 xueqiu_common 旧 DDL），
    雪球 status id 全平台唯一，现实中不会跨符号撞 id，故此处用不同 id。"""
    store, cursor, _ = _env()
    pages = [{"items": [_item(1)]}, {"items": []},   # SH600519
             {"items": [_item(2)]}, {"items": []}]   # SZ000001
    sess = FakeSession(pages=pages)
    res = collect_comments(sess, store, cursor,
                           symbols=["SH600519", "SZ000001"], max_pages=2)
    assert res["new"] == 2
    assert store.count("xq_comment") == 2
    syms = {r[0] for r in store.con.execute("SELECT symbol FROM xq_comment")}
    assert syms == {"SH600519", "SZ000001"}
    store.close()
